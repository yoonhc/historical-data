import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import deployment as d
import handoff as h
import installation as i
import readiness


def test_seal_inspection_does_not_require_or_grant_production_approval():
    plan = d.load_checked()
    assert len(plan["inner"]["operations"]) == 13
    args = SimpleNamespace(approve_plan=None, action="apply", request_review=None, approve_review_hash=None)
    with pytest.raises(h.c.Hold, match="Administrator"):
        d.approval_check(plan, args, uid=1000)
    with pytest.raises(h.c.Hold, match="exact-plan"):
        d.approval_check(plan, args, uid=0)


def test_rollback_review_requires_exact_explicit_hash_and_direction(tmp_path):
    plan = d.load_checked()
    p = tmp_path / "review.json"
    p.write_bytes(h.c.encode({"plan_id": plan["plan_id"], "action": "preserve-for-reviewed-rollback", "reason": "test"}))
    args = SimpleNamespace(approve_plan=plan["plan_id"], action="apply", request_review=p, approve_review_hash=h.c.sha(p.read_bytes()))
    with pytest.raises(h.c.Hold, match="rollback"):
        d.approval_check(plan, args, uid=0)
    args.action = "rollback"
    assert d.approval_check(plan, args, uid=0)["reason"] == "test"
    args.approve_review_hash = "f" * 64
    with pytest.raises(h.c.Hold, match="hash differs"):
        d.approval_check(plan, args, uid=0)


def entry(path):
    return {"kind": "file", "mode": 0o644, "sha256": h.c.sha(path.read_bytes()), "source": str(path)}


def test_offline_install_resume_and_no_overwrite(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"a"); b.write_bytes(b"b")
    entries = {"variant/a": entry(a), "variant/b": entry(b), "variant/python": {"kind": "symlink", "target": "/usr/bin/python3"}}
    dest = tmp_path / "fixed"
    original = i._create
    def interrupted(path, raw, mode):
        if path.name == "b":
            raise OSError("interrupted before second file")
        original(path, raw, mode)
    monkeypatch.setattr(i, "_create", interrupted)
    with pytest.raises(OSError):
        i.install(dest, entries, identity="fixed", live=False)
    assert (dest / "variant/a").read_bytes() == b"a"
    monkeypatch.setattr(i, "_create", original)
    assert i.install(dest, entries, identity="fixed", live=False)["sealed"]
    assert (dest / "variant/a").stat().st_mode & 0o777 == 0o644
    assert os.readlink(dest / "variant/python") == "/usr/bin/python3"
    i.install(dest, entries, identity="fixed", live=False)
    (dest / "variant/a").write_bytes(b"changed")
    with pytest.raises(h.c.Hold, match="differs"):
        i.install(dest, entries, identity="fixed", live=False)
    assert (dest / "variant/a").read_bytes() == b"changed"


@pytest.mark.parametrize("bad", ["identity", "source", "extra", "leaf-link", "parent-link"])
def test_installation_refuses_unknown_or_unsealed_state(tmp_path, bad):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    entries = {"a": entry(source)}
    dest = tmp_path / "install"
    i.install(dest, entries, identity="fixed", live=False)
    if bad == "identity":
        (dest / ".install-intent.json").write_text('{}')
    elif bad == "source":
        source.write_bytes(b"changed")
    elif bad == "extra":
        (dest / "extra").touch()
    elif bad == "leaf-link":
        (dest / "a").unlink(); (dest / "a").symlink_to(source)
    else:
        linked = tmp_path / "linked"
        linked.symlink_to(dest, target_is_directory=True)
        dest = linked
    with pytest.raises(h.c.Hold):
        i.install(dest, entries, identity="fixed", live=False)
    assert source.read_bytes() in (b"original", b"changed")


def test_readiness_deferred_only_unknown_failure_stays_failure(capsys):
    class Deferred(Exception):
        pass
    def pending():
        raise Deferred("observation unavailable")
    assert readiness.evaluate(pending, Deferred) == 75
    assert json.loads(capsys.readouterr().out)["ready"] is False
    def broken():
        raise OSError("disk")
    with pytest.raises(OSError):
        readiness.evaluate(broken, Deferred)
    assert readiness.evaluate(lambda: 0, Deferred) == 0


@pytest.mark.parametrize("change", ["unit", "mode", "external"])
def test_native_host_guard_detects_drift_before_work(tmp_path, monkeypatch, change):
    # Native guard itself, with only metadata/boot/FS locations redirected.
    plan = d.load_checked()
    plan = copy.deepcopy(plan)
    p = tmp_path / "input"
    p.write_bytes(b"fixed")
    plan["inner"]["protected"] = {"/input": {"kind": "digest", "sha256": h.c.sha(b"fixed")}}
    plan["inner"]["retirement_masks"] = {}
    plan["external_files"] = {"/input": h.c.sha(b"fixed")}
    plan["protected_metadata"] = {"/input": h.c.metadata(p)}
    fs = h.c.SafeFiles(tmp_path, (), {})
    guard = h.Host(plan, fs, None)
    guard.immutable()
    if change == "unit":
        p.write_bytes(b"changed")
    elif change == "mode":
        p.chmod(0o600)
    else:
        plan["external_files"]["/input"] = "0" * 64
    with pytest.raises(h.c.Hold):
        guard.immutable()


def test_forbidden_unit_mutations_never_reach_systemctl():
    calls = []
    m = h.c.Managers(allowed=[("system", n) for n in (*h.c.SERVICES, *h.c.TIMERS, h.NEW_WAITER)],
                     runner=lambda *a, **k: calls.append(a))
    for manager, name in (("system", h.c.WAITER), ("system", "chrony.service"),
                          ("system", "marketdata-collector@depth.service"), ("user", h.c.SERVICES[0]),
                          ("system", "marketdata-checkpoint-retirement.timer")):
        with pytest.raises(h.c.Hold, match="scope"):
            m.command(manager, "stop", name)
    assert calls == []
