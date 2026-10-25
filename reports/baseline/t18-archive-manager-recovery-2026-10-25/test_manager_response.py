import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("manager_response_repair", HERE / "repair_resume.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
n, op, c = r.n, r.op, r.c
KEYS = ("ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecCondition")


def responses(monkeypatch, property_value=None, object_value=None):
    host = object.__new__(n.Host)
    seen = []

    def run(args):
        seen.append(args)
        if "GetUnit" in args:
            return json.dumps(object_value if object_value is not None else {"type": "o", "data": ["/org/freedesktop/systemd1/unit/checked_service"]})
        return json.dumps(property_value if property_value is not None else {"type": "a(sasbttttuii)", "data": []})

    monkeypatch.setattr(n, "run", run)
    return host, seen


def test_missing_text_requires_actual_empty_typed_properties(monkeypatch):
    host, seen = responses(monkeypatch)
    assert not any(host.optional_commands({}).values())
    assert len(seen) == 6
    assert {args[-1] for args in seen if "get-property" in args} == set(KEYS)


def test_nonempty_typed_command_is_not_accepted(monkeypatch):
    host, seen = responses(monkeypatch, {"type": "a(sasbttttuii)", "data": [["unexpected"]]})
    assert any(host.optional_commands({}).values())


def test_present_nonempty_text_is_not_erased(monkeypatch):
    host, seen = responses(monkeypatch)
    row = {key: "" for key in KEYS}
    row["ExecStartPre"] = "unexpected command"
    assert host.optional_commands(row)["ExecStartPre"] == "unexpected command"
    assert not seen


@pytest.mark.parametrize("value", [{}, {"type": "s", "data": []}, {"type": "a(sasbttttuii)", "data": None}])
def test_malformed_typed_command_rejected(monkeypatch, value):
    host, seen = responses(monkeypatch, value)
    with pytest.raises(op.Hold, match="Invalid command property"):
        host.optional_commands({})


@pytest.mark.parametrize("value", [{}, {"type": "o", "data": []}, {"type": "o", "data": ["wrong/path"]}])
def test_bad_unit_lookup_rejected(monkeypatch, value):
    host, seen = responses(monkeypatch, object_value=value)
    with pytest.raises(op.Hold, match="Invalid service object"):
        host.optional_commands({})


def test_dependency_reordering_only_is_equal():
    a = {"collector": {"MainPID": "7", "InvocationID": "same", "ActiveState": "active", "After": "a b", "Requires": "b c"}}
    b = {"collector": {**a["collector"], "After": "b a", "Requires": "c b"}}
    assert r.normalized_units(a) == r.normalized_units(b)
    for key, value in (("MainPID", "8"), ("InvocationID", "new"), ("ActiveState", "failed"), ("After", "a c"), ("Requires", "b d")):
        changed = {"collector": {**b["collector"], key: value}}
        assert r.normalized_units(a) != r.normalized_units(changed)


def test_exact_pending_transaction_is_accepted_without_erasure(tmp_path, monkeypatch):
    monkeypatch.setattr(n, "safe", lambda path: path)
    for name in ("state.json", "transaction.json", "authority.json"):
        (tmp_path / name).write_text(name)
    repair = {"initial_records": {name: c.file_sha(tmp_path / name) for name in ("state.json", "transaction.json", "authority.json")}, "absent_records": ["apply-receipt.json", "rollback-receipt.json"]}
    r.initial_boundary(repair, tmp_path)
    assert (tmp_path / "transaction.json").read_text() == "transaction.json"
    (tmp_path / "transaction.json").write_text("other")
    with pytest.raises(op.Hold):
        r.initial_boundary(repair, tmp_path)


@pytest.mark.parametrize("direction", ["apply", "rollback"])
def test_pending_after_files_preserves_authority_and_journal(tmp_path, direction):
    path = HERE.parent / "t18-archive-deploy-2026-10-22/tests/test_operation.py"
    spec = importlib.util.spec_from_file_location("pending_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    fixture.PREPARED = json.loads((HERE / "prepared-plan.json").read_bytes())
    plan = json.loads((HERE / "operation-plan.json").read_bytes())
    host = fixture.Fixture(tmp_path)
    point = "file:" + sorted(plan["prepared"]["changes"])[-1]
    with pytest.raises(fixture.Crash):
        op.Operation(plan, host, fault=fixture.crash_at(point)).run("apply")
    authority = host.raw("/records/authority.json")
    host.write("/records/failed-attempt.json", b"original failed native property\n")
    assert json.loads(host.raw(c.LEDGER))["schema"] == 2
    result = op.Operation(plan, host).run(direction)
    assert host.raw("/records/authority.json") == authority
    assert host.raw("/records/failed-attempt.json") == b"original failed native property\n"
    assert host.events.count("authority_timestamp") == 1
    if direction == "apply":
        assert result["phase"] == "activated"
        assert len(host.started) == 9
    else:
        assert result["phase"] == "rolled_back_archiver_held"
        assert len(host.started) == 8 and c.TIMER not in host.started
    assert json.loads(host.raw(c.LEDGER))["step_attempts"] == fixture.PREPARED["ledger_before"]["step_attempts"]
