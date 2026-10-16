"""Only the new approval/install/import orchestration; no operating mutations."""
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("t15_apply", Path(__file__).with_name("apply_scheduled.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

ROOT = Path("/opt/marketdata/releases/fixed-test-release")
ENTRIES = {
    ".venv/lib/python3.12/site-packages/marketdata/__init__.py": {"sha256": "1" * 64},
    ".venv/lib/python3.12/site-packages/marketdata/cli.py": {"sha256": "2" * 64},
}
IMPORT = {
    "uid": 1000, "executable": str(ROOT / ".venv/bin/python"), "prefix": str(ROOT / ".venv"),
    "modules": {name: {"path": str(ROOT / relative), "sha256": entry["sha256"]}
                for name, (relative, entry) in zip(("marketdata", "marketdata.cli"), ENTRIES.items())},
}


@pytest.mark.parametrize("change", ("uid", "prefix", "path", "sha256"))
def test_rejects_wrong_installed_import(change):
    value = copy.deepcopy(IMPORT)
    if change == "uid":
        value["uid"] = 0
    elif change == "prefix":
        value["prefix"] = "/tmp/old-environment"
    else:
        value["modules"]["marketdata.cli"][change] = "/tmp/wrong-module" if change == "path" else "f" * 64
    with pytest.raises(RuntimeError):
        app.validate_import(value, ROOT, ENTRIES)


def simulate(monkeypatch, capsys, *, busy=False, bad_import=False, started=False):
    events = []
    documents = {}
    directory = "/var/lib/marketdata-clock/scheduled-handoffs/" + app.PLAN_ID
    if started:
        documents[directory + "/apply.json"] = {"phase": "held"}

    class FS:
        def node(self, path):
            return {"kind": "file" if path in documents else "absent"}

        def json(self, path):
            return documents[path]

        def save(self, path, value, *, immutable=False):
            if immutable and path in documents:
                assert documents[path] == value
            documents[path] = copy.deepcopy(value)

    fs = FS()

    class Managers:
        def states(self, manager, names):
            return {name: {"classification": "running" if busy else "idle"} for name in names}

        def show(self, manager, name):
            return {"MainPID": "123", "InvocationID": "same", "ActiveState": "active"}

    class Host:
        def __init__(self, *args):
            pass

        def clock_ready(self):
            return True

    @contextlib.contextmanager
    def setup(*args):
        events.append("setup_lock")
        try:
            yield
        finally:
            events.append("setup_unlock")

    def install(root):
        assert root == ROOT
        events.append("install")
        return {"files": 2}

    def import_run(*args, **kwargs):
        events.append("import")
        value = copy.deepcopy(IMPORT)
        if bad_import:
            value["modules"]["marketdata.cli"]["path"] = "/tmp/trial/marketdata/cli.py"
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")

    def handoff(plan, args):
        assert documents[directory + "/approval.json"]["enabled"] is True
        assert documents[directory + "/deployment/installed-import.json"] == IMPORT
        assert events[-1] == "setup_unlock"
        assert args.action == "apply" and args.drain_seconds == 600
        events.append("handoff")
        documents[directory + "/apply.json"] = {"phase": "complete", "result": {"operating_observation_pending": True}}

    c = SimpleNamespace(SafeFiles=lambda *a, **k: fs, Managers=Managers, IDENTITIES=("chrony", "depth", "aggtrade"),
                        encode=lambda x: json.dumps(x, sort_keys=True).encode(),
                        sha=lambda x: hashlib.sha256(x).hexdigest(), user_command=lambda argv: argv)
    engine = SimpleNamespace(install_release=install, environment_files=lambda: ENTRIES, live_execution=handoff)
    monkeypatch.setattr(app.os, "geteuid", lambda: 0)
    monkeypatch.setattr(app, "check_before", lambda *args: events.append("before_check"))
    monkeypatch.setattr(app, "setup_lock", setup)
    monkeypatch.setattr(app.subprocess, "run", import_run)
    # Keep the orchestration test bounded; the observer-only wait is tested below.
    def preparation(host, managers):
        app.require_quiet(managers.states("user", app.CRITICAL))
        return {"strict_ready": host.clock_ready(), "wait_seconds": 0}
    monkeypatch.setattr(app, "wait_before_install", preparation)
    code = app.run(c, engine, Host, "/var/lib/marketdata-clock/scheduled-handoffs",
                   {"inner": {"release_id": ROOT.name}}, {"enabled": True})
    capsys.readouterr()
    return code, events, documents


def test_final_import_before_handoff_and_setup_lock_released(monkeypatch, capsys):
    code, events, _ = simulate(monkeypatch, capsys)
    assert code == 0
    assert events.index("install") < events.index("import") < events.index("setup_unlock") < events.index("handoff")


def test_import_mismatch_keeps_existing_jobs_untouched(monkeypatch, capsys):
    code, events, docs = simulate(monkeypatch, capsys, bad_import=True)
    assert code == 75 and "handoff" not in events
    assert not any(path.endswith("/apply.json") for path in docs)
    assert any(v.get("phase") == "installing_fixed_release" and "error" in v for v in docs.values())


def test_busy_backup_is_not_interrupted(monkeypatch, capsys):
    code, events, _ = simulate(monkeypatch, capsys, busy=True)
    assert code == 75
    assert "install" not in events and "handoff" not in events


def test_partial_handoff_requires_recorded_resume_not_fresh_apply(monkeypatch, capsys):
    code, events, docs = simulate(monkeypatch, capsys, started=True)
    assert code == 75 and events == []
    assert docs["/var/lib/marketdata-clock/scheduled-handoffs/" + app.PLAN_ID + "/apply.json"] == {"phase": "held"}


def test_readiness_timeout_does_not_change_managers(capsys):
    elapsed = [0]
    observed = []
    managers = SimpleNamespace(states=lambda manager, names: {
        name: {"classification": "idle"} for name in names})
    host = SimpleNamespace(clock_ready=lambda: False)
    with pytest.raises(RuntimeError, match="existing jobs remain unchanged"):
        app.wait_before_install(host, managers, timeout=20,
                                monotonic=lambda: elapsed[0],
                                pause=lambda seconds: (observed.append(seconds), elapsed.__setitem__(0, elapsed[0] + seconds)))
    assert sum(observed) == 20
    assert '"old_jobs_unchanged": true' in capsys.readouterr().out
