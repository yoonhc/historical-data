import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from marketdata import job_clock as jc
from test_clock_decisions import Samples, provider, load_runner


@pytest.mark.parametrize("failure", [OSError("disk"), KeyError("unexpected"), TypeError("bug")])
def test_non_time_query_error_is_fatal_and_cannot_be_reset(failure):
    s = Samples()
    c = jc.Context(lambda: (_ for _ in ()).throw(failure), lambda _: None, sampler=s)
    with pytest.raises(jc.ClockFailure) as caught:
        c.point("entry")
    assert caught.value.__cause__ is failure
    with pytest.raises(jc.ClockFailure):
        c.restart_observation()
    with pytest.raises(jc.ClockFailure):
        c.close()


def test_success_observation_journal_failure_never_becomes_time_wait():
    s = Samples()
    kinds = []

    def emit(event):
        kinds.append(event["kind"])
        raise OSError("journal disk error")

    c = jc.Context(lambda: provider(s), emit, sampler=s)
    with pytest.raises(jc.ClockFailure, match="evidence_write_failed"):
        c.point("normal")
    assert kinds == ["time_decision"]
    with pytest.raises(jc.ClockFailure):
        c.wait_point("retry")


@pytest.mark.parametrize(
    "sample_error", [jc.ClockDeferred("sample_gap"), OSError("sampling"), SystemExit(9)]
)
def test_dead_monitor_never_resumes_payload(sample_error):
    s = Samples()

    def sample():
        if threading.current_thread().name == "scheduled-clock":
            raise sample_error
        return s()

    def emit(event):
        if event.get("reason") == "monitor":
            raise OSError("failure journal also failed")

    c = jc.Context(lambda: provider(s), emit, sampler=sample, monitor_interval=0.01)
    c.start()
    c.thread.join(1)
    assert not c.thread.is_alive()
    calls = []
    with pytest.raises(jc.ClockFailure):
        load_runner().run_with_resume(c, lambda: calls.append(1), sleep=lambda _: None)
    with pytest.raises(jc.ClockFailure):
        c.restart_observation()
    with pytest.raises(jc.ClockFailure):
        c.start()
    with pytest.raises(jc.ClockFailure):
        c.close()
    assert calls == [] and c.fatal is not None


def test_expected_monitor_time_fault_reuses_living_monitor_and_records_gap():
    s = Samples()
    failed = threading.Event()
    events = []

    def sample():
        if threading.current_thread().name == "scheduled-clock" and not failed.is_set():
            failed.set()
            raise jc.ClockDeferred("one_time_gap")
        return s()

    c = jc.Context(lambda: provider(s), events.append, sampler=sample, monitor_interval=0.01)
    try:
        c.start()
        assert failed.wait(1)
        thread = c.thread
        with pytest.raises(jc.ClockDeferred):
            c.point("held")
        c.restart_observation()
        c.start()
        c.point("new_query")
        assert c.thread is thread and thread.is_alive()
        assert any(e["kind"] == "observation_restart" for e in events)
    finally:
        c.close()


def test_real_blocked_monitor_close_is_bounded_and_fatal():
    s = Samples()
    entered, release = threading.Event(), threading.Event()

    def sampler():
        if threading.current_thread().name == "scheduled-clock":
            entered.set()
            release.wait(2)
        return s()

    c = jc.Context(
        lambda: provider(s),
        lambda _: None,
        sampler=sampler,
        monitor_interval=0.01,
        stop_timeout=0.01,
    )
    try:
        c.start()
        assert entered.wait(1)
        with pytest.raises(jc.ClockFailure, match="monitor_stop_timeout"):
            c.close()
        assert c.thread.is_alive()
    finally:
        release.set()
        c.thread.join(1)


@pytest.fixture
def wrapper(tmp_path, monkeypatch, store):
    runner = load_runner()
    s = Samples()
    binding = tmp_path / "binding.json"
    binding.write_text("{}")
    monkeypatch.setattr(runner, "query_factory", lambda _: lambda: provider(s))
    original = jc.Context
    monkeypatch.setattr(runner, "ROOT", Path(__file__).resolve().parents[1] / "candidate")
    # Preserve real Context/thread; only time provider/sampler are isolated.
    monkeypatch.setattr(
        jc, "Context", lambda *a, **kw: original(*a, sampler=s, monitor_interval=0.01, **kw)
    )
    args = ["--root", str(store.root), "oi-quality", "--days", "7"]

    def run(job="oi-quality", work_args=None):
        monkeypatch.setattr(
            "sys.argv",
            [
                "run_job",
                "--job",
                job,
                "--binding",
                str(binding),
                "--state-root",
                str(tmp_path / "requests"),
                "--run",
                "--",
                *(args if work_args is None else work_args),
            ],
        )
        return runner.main()

    def request(job="oi-quality"):
        return json.loads((tmp_path / "requests" / job / "request.json").read_bytes())

    return SimpleNamespace(
        run=run,
        request=request,
        runner=runner,
        context_class=original,
        root=tmp_path,
        store=store,
        samples=s,
    )


def test_real_quality_findings_are_terminal_and_argparse_two_is_not(wrapper, monkeypatch):
    assert wrapper.run() == 2
    first = wrapper.request()
    assert first["state"] == "complete"
    assert first["disposition"]["reason"] == "report_written_with_findings"
    with pytest.raises(BaseExceptionGroup):
        wrapper.run(work_args=["oi-quality", "--unknown-argument"])
    last = wrapper.request()
    assert last["state"] == "failed" and last["exit_code"] == 1
    assert last["attempt"]["work"]["observed_exit_code"] == 2


def test_close_failure_saves_work_and_recovers_without_repeating_quality(wrapper, monkeypatch):
    original = wrapper.context_class.close

    def bad_close(c):
        original(c)
        raise jc.ClockFailure("injected_close_failure")

    monkeypatch.setattr(wrapper.context_class, "close", bad_close)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    failed = wrapper.request()
    assert failed["state"] == "finalization_failed" and failed["exit_code"] == 1
    assert failed["work_exit_code"] == 2 and failed["work_result"]
    assert failed["attempt"]["errors"][0]["phase"] == "monitor_close"
    monkeypatch.setattr(wrapper.context_class, "close", original)
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *a: pytest.fail("must reconcile stored report")
    )
    assert wrapper.run() == 2
    result = wrapper.request()
    assert result["state"] == "complete" and result["pins"] == failed["pins"]
    assert result["attempt"]["work"]["recovered_committed_result"]
    assert (
        len(
            list(
                (wrapper.root / "requests/oi-quality/attempts" / result["request_id"]).glob(
                    "*.json"
                )
            )
        )
        == 2
    )


def test_work_and_close_errors_are_both_recorded(wrapper, monkeypatch):
    original = wrapper.context_class.close
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *a: (_ for _ in ()).throw(ValueError("work_bug"))
    )

    def close(c):
        original(c)
        raise jc.ClockFailure("close_bug")

    monkeypatch.setattr(wrapper.context_class, "close", close)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    r = wrapper.request()
    assert [e["phase"] for e in r["attempt"]["errors"]] == ["work", "monitor_close"]
    assert r["state"] == "failed" and r["pins"] == {}


@pytest.mark.parametrize("bad_status", [False, 0.0])
def test_invalid_payload_status_is_not_a_successful_process_exit(wrapper, monkeypatch, bad_status):
    monkeypatch.setattr(wrapper.runner, "invoke", lambda *a: bad_status)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    assert wrapper.request()["state"] == "failed"
    assert wrapper.request()["exit_code"] == 1


def test_final_state_io_error_is_not_success_and_payload_is_not_repeated(wrapper, monkeypatch):
    original = wrapper.runner.atomic

    def fail_terminal(path, value):
        if path.name == "request.json" and value.get("state") == "complete":
            raise OSError("injected_final_fsync")
        return original(path, value)

    monkeypatch.setattr(wrapper.runner, "atomic", fail_terminal)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    r = wrapper.request()
    assert r["state"] == "finalization_failed" and r["exit_code"] == 1
    assert r["work_result"] and r["attempt"]["errors"][-1]["phase"] == "request_state"
    monkeypatch.setattr(wrapper.runner, "atomic", original)
    monkeypatch.setattr(wrapper.runner, "invoke", lambda *a: pytest.fail("duplicate payload"))
    assert wrapper.run() == 2


def test_process_kill_after_committed_payload_recovers_without_invoking_it(wrapper, monkeypatch):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            str(Path(__file__).with_name("kill_after_result.py")),
            str(wrapper.store.root),
            str(wrapper.root),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()
    before = wrapper.request()
    assert before["state"] == "result_recorded" and before["work_result"]
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *a: pytest.fail("committed payload repeated")
    )
    assert wrapper.run() == 2
    after = wrapper.request()
    assert after["request_id"] == before["request_id"] and after["pins"] == before["pins"]
    assert after["attempt"]["work"]["observed_exit_code"] is None
    history = wrapper.root / "requests/oi-quality/attempts" / after["request_id"]
    recovered = list(history.glob("recovered-state-*.json"))
    assert len(recovered) == 1 and json.loads(recovered[0].read_bytes()) == before


def test_all_result_and_final_writes_fail_without_claiming_success(wrapper, monkeypatch):
    original = wrapper.runner.atomic
    failures = []

    def fail_after_work(path, value):
        if value.get("work_result") or value.get("unsealed_result"):
            failures.append(str(path))
            raise OSError("all final paths unavailable")
        original(path, value)

    monkeypatch.setattr(wrapper.runner, "atomic", fail_after_work)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    r = wrapper.request()
    assert r["state"] == "pending" and "oi_quality_day" in r["pins"]
    assert any("/attempts/" in p for p in failures)
    assert failures.count(str(wrapper.root / "requests/oi-quality/request.json")) >= 3


def test_same_config_path_with_new_contents_cannot_resume_old_scope(wrapper, monkeypatch):
    config = wrapper.root / "oi.toml"
    config.write_text("scope='first'\n")
    args = ["update", "--config", str(config)]
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *a: (_ for _ in ()).throw(RuntimeError("unfinished"))
    )
    with pytest.raises(BaseExceptionGroup):
        wrapper.run("oi", args)
    original = wrapper.request("oi")
    config.write_text("scope='changed'\n")
    with pytest.raises(RuntimeError, match="Pending scope differs"):
        wrapper.run("oi", args)
    assert wrapper.request("oi") == original


def test_configuration_change_during_time_wait_is_fatal(wrapper, monkeypatch):
    config = wrapper.root / "oi.toml"
    config.write_text("scope='first'\n")

    def work(*a):
        config.write_text("scope='changed'\n")
        jc.active().point("next_http")
        pytest.fail("changed work must not proceed")

    monkeypatch.setattr(wrapper.runner, "invoke", work)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run("oi", ["update", "--config", str(config)])
    r = wrapper.request("oi")
    assert (
        r["state"] == "failed" and "configuration changed" in r["attempt"]["errors"][0]["message"]
    )


@pytest.mark.parametrize(
    "message,retriable",
    [
        ("Chrony has no active invocation", True),
        ("Clock bootstrap/configuration transition in progress", True),
        ("Invalid Chrony fields", True),
        ("Chrony configuration differs", False),
        ("Untrusted clock/approval path: /x", False),
        ("unexpected bug", False),
    ],
)
def test_legacy_host_adapter_retries_only_known_time_holds(message, retriable):
    from contextlib import contextmanager

    class Hold(RuntimeError):
        pass

    class Host:
        def __init__(self, binding):
            pass

        @contextmanager
        def lock(self):
            raise Hold(message)
            yield

    query = load_runner().observation_query(SimpleNamespace(Host=Host, ClockHold=Hold), {})
    with pytest.raises(jc.ClockDeferred if retriable else Hold):
        query()
