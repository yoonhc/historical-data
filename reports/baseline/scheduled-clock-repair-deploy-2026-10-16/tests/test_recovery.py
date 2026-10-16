import fcntl
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from marketdata import job_clock as jc
from test_clock_decisions import Samples, provider
import recover_request as r

HERE = Path(__file__).resolve().parents[1]


def rehash(plan):
    plan["plan_id"] = r.sha(r.encode({k: v for k, v in plan.items() if k != "plan_id"}))


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    plan = json.loads((HERE / "recovery-plan.json").read_bytes())
    state = tmp_path / "state/oi-quality"
    state.mkdir(parents=True)
    (state / "request.lock").touch()
    (state / "request.tmp").write_bytes(b"original failed temporary file")
    for name, target in [
        ("request.json", state / "request.json"),
        ("report.json", tmp_path / "report.json"),
        ("decisions.jsonl", state / "decisions.jsonl"),
    ]:
        target.write_bytes((HERE / "recovery-inputs" / name).read_bytes())
    plan.update(
        request_path=str(state / "request.json"),
        report_path=str(tmp_path / "report.json"),
        journal_path=str(state / "decisions.jsonl"),
    )
    rehash(plan)
    wrapper, contract = r.modules(plan)
    monkeypatch.setattr(
        wrapper, "invoke", lambda *args: pytest.fail("No workload invocation allowed")
    )
    from marketdata import cli, oi_quality

    monkeypatch.setattr(cli, "main", lambda *args: pytest.fail("No CLI retry allowed"))
    monkeypatch.setattr(
        oi_quality,
        "quality_report",
        lambda *args, **kw: pytest.fail("No report recomputation allowed"),
    )
    lab = SimpleNamespace(
        plan=plan,
        contract=contract,
        original=(state / "request.json").read_bytes(),
        state=state,
        monitors=0,
        guards=0,
        events=[],
        fail=None,
    )

    def guard():
        lab.guards += 1
        if lab.fail == "scope":
            raise r.Hold("scope changed")

    def monitor(emit, check):
        lab.monitors += 1
        s = Samples("2026-10-16T14:30:00Z")

        def record(value):
            if lab.fail == "record":
                raise OSError("recording unavailable")
            emit(value)
            lab.events.append(value)

        context = jc.Context(
            lambda: provider(s), record, sampler=s, scope_guard=check, monitor_interval=0.001
        )
        try:
            context.start()
            assert context.thread.is_alive()
            context.point("explicit_stored_result_reconciliation")
            context.check_health()
        finally:
            value = context.close()
        if lab.fail == "close":
            raise jc.ClockFailure("close failed")
        return {**value, "ok": True}

    lab.guard, lab.monitor = guard, monitor

    def run(**kwargs):
        return r.recover(
            lab.plan,
            lab.contract,
            lab.guard,
            lab.monitor,
            approval={
                "enabled": True,
                "recovery_plan_id": lab.plan["plan_id"],
                "host_plan_id": "isolated",
                "reference": "isolated-approval",
            },
            **kwargs,
        )

    lab.run = run
    return lab


def test_original_scope_recovery_preserves_failure_report_and_quality(recovery):
    x = recovery
    before_report = r.read(x.plan["report_path"])
    before_journal = r.read(x.plan["journal_path"])
    receipt = x.run()
    after = json.loads(r.read(x.plan["request_path"]))
    original = json.loads(x.original)
    for key in ("request_id", "identity", "scope", "pins", "arguments"):
        assert after[key] == original[key]
    assert after["state"] == "complete" and after["work_result"]["result"]["needs_review"]
    assert after["attempt"]["work"]["observed_exit_code"] is None
    assert "work_exit_code" not in after
    assert receipt["original_process_exit_code"] == 1 and receipt["derived_result_code"] == 2
    assert receipt["work_invocations"] == 0
    assert (
        r.read(x.plan["report_path"]) == before_report
        and r.read(x.plan["journal_path"]) == before_journal
    )
    assert (x.state / "request.tmp").read_bytes() == b"original failed temporary file"
    assert x.run() == receipt and x.monitors == 1
    assert len(list((x.state / "attempts" / original["request_id"]).glob("*.json"))) == 1


@pytest.mark.parametrize(
    "cut",
    [
        "evidence_preserved",
        "monitor_completed",
        "terminal_staged",
        "attempt_saved",
        "request_committed",
        "receipt_saved",
    ],
)
def test_actual_process_kill_and_repeat_do_not_recompute_or_duplicate(recovery, cut):
    x = recovery
    plan_file = x.state.parent / "isolated-plan.json"
    plan_file.write_bytes(r.encode(x.plan))
    process = subprocess.run(
        [sys.executable, "-B", "-I", str(HERE / "tests/kill_worker.py"), str(plan_file), cut],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert process.returncode == -signal.SIGKILL, process.stderr
    receipt = x.run()
    current = r.read(x.plan["request_path"])
    assert x.run() == receipt and r.read(x.plan["request_path"]) == current
    assert receipt["work_invocations"] == 0
    assert len(list((x.state / "attempts" / x.plan["request_id"]).glob("*.json"))) == 1


@pytest.mark.parametrize("kind", ["scope", "record", "close"])
def test_monitor_or_configuration_failure_leaves_original_pending(recovery, kind):
    x = recovery
    x.fail = kind
    with pytest.raises((r.Hold, jc.ClockFailure, OSError)):
        x.run()
    assert r.read(x.plan["request_path"]) == x.original
    assert not list((x.state / "attempts").glob("*/*.json"))
    x.fail = None
    assert x.run()["request_state"] == "complete"


@pytest.mark.parametrize(
    "kind",
    ["request", "report", "journal", "report_identity", "observation", "request_identity", "pins"],
)
def test_changed_original_inputs_never_repaired_by_date_or_latest(recovery, kind):
    x = recovery
    if kind in ("request", "report", "journal"):
        key = "journal_path" if kind == "journal" else kind + "_path"
        raw = r.read(x.plan[key])
        Path(x.plan[key]).write_bytes(b"X" + raw[1:] if kind == "journal" else raw + b" ")
    elif kind in ("report_identity", "observation"):
        report = json.loads(r.read(x.plan["report_path"]))
        if kind == "report_identity":
            report["report_id"] = "f" * 64
        else:
            report["clock_observation"]["provider"]["invocation_id"] = "different"
            from marketdata.domain import json_bytes

            report["report_id"] = r.sha(
                json_bytes({k: v for k, v in report.items() if k != "report_id"})
            )
            x.plan["report_id"] = report["report_id"]
        raw = r.encode(report)
        Path(x.plan["report_path"]).write_bytes(raw)
        x.plan["report_sha256"] = r.sha(raw)
        rehash(x.plan)
    elif kind == "request_identity":
        x.plan["identity"] = "f" * 64
        rehash(x.plan)
    else:
        x.plan["pins"]["oi_quality_day"]["day"] = "2026-10-17"
        rehash(x.plan)
    current = r.read(x.plan["request_path"])
    with pytest.raises((r.Hold, x.contract.RequestError)):
        x.run()
    assert r.read(x.plan["request_path"]) == current and x.monitors == 0


def test_lock_contention_preserves_pending_and_new_attempts(recovery):
    x = recovery
    with (x.state / "request.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            x.run()
    assert r.read(x.plan["request_path"]) == x.original
    assert x.run()["request_state"] == "complete"


def test_future_request_is_never_overwritten_by_repeated_repair(recovery):
    x = recovery
    x.run()
    request = json.loads(r.read(x.plan["request_path"]))
    request["request_id"] = "new-legitimate-request"
    current = x.contract.encode(request)
    Path(x.plan["request_path"]).write_bytes(current)
    with pytest.raises(r.Hold, match="changed after recovery"):
        x.run()
    assert r.read(x.plan["request_path"]) == current


def test_journal_append_keeps_exact_original_prefix(recovery):
    x = recovery
    with Path(x.plan["journal_path"]).open("ab") as stream:
        stream.write(b'{"kind":"later unrelated observation"}\n')
    assert x.run()["work_invocations"] == 0


def test_approval_does_not_follow_from_a_readonly_review(recovery):
    x = recovery
    with pytest.raises(r.Hold, match="approval"):
        r.recover(
            x.plan,
            x.contract,
            x.guard,
            x.monitor,
            approval={"enabled": False, "recovery_plan_id": x.plan["plan_id"]},
        )
    assert r.read(x.plan["request_path"]) == x.original


def test_no_monitor_thread_left_after_success_or_failed_close(recovery):
    x = recovery
    x.fail = "close"
    with pytest.raises(jc.ClockFailure):
        x.run()
    assert not any(t.name == "scheduled-clock" for t in threading.enumerate())
    x.fail = None
    x.run()
    assert not any(t.name == "scheduled-clock" for t in threading.enumerate())
