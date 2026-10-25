import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "work"), str(HERE / "work/packages/marketdata/src")]
from scripts import clock_retirement as safe  # noqa: E402

spec = importlib.util.spec_from_file_location("recurring_preflight", HERE / "system_preflight.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_transient_properties_preserve_safeguards_and_isolate_writes():
    source = (HERE / "deployment/marketdata-checkpoint-retirement.service").read_text()
    props = trial.service_properties(source, Path("/var/lib/md-t05-recurring-test"))
    required = (
        "User=yoonhc",
        "Group=yoonhc",
        "Type=oneshot",
        "ProtectClock=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "Restart=no",
        "CapabilityBoundingSet=",
        "NoNewPrivileges=yes",
        "KillMode=mixed",
    )
    assert all(v in props for v in required)
    writable = [v for v in props if v.startswith("ReadWritePaths=")]
    assert writable == ["ReadWritePaths=/var/lib/md-t05-recurring-test"]
    assert [v for v in props if v.startswith("Environment=")] == [
        "Environment=LC_ALL=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
    ]
    assert not any(v.startswith("ExecStart=") for v in props)


def test_duplicate_scalar_manager_property_rejected(monkeypatch):
    monkeypatch.setattr(
        trial, "command", lambda *a, **k: SimpleNamespace(stdout="MainPID=1\nMainPID=2\n")
    )
    with pytest.raises(RuntimeError, match="Duplicate scalar"):
        trial.show("md-t05-recurring-test.service", "MainPID")


@pytest.mark.parametrize("changed", (None, "input", "operating", "source"))
def test_preflight_rejects_changed_inputs_before_trial_creation(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(trial, "HERE", tmp_path)
    (tmp_path / "work").mkdir()
    (tmp_path / "work/source.py").write_text("original source")
    (tmp_path / "input").write_text("original tool")
    (tmp_path / "operating").write_text("original config")
    manifest = {"release_id": "test", "source_sha256": {"source.py": trial.sha(b"original source")}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "environment-files.json").write_text("{}")
    (tmp_path / "validation.json").write_text(
        json.dumps({"passed": True, "candidate_cases": 46, "preflight_tool_cases": 15})
    )
    (tmp_path / "reproduction.json").write_text(json.dumps({"passed": True}))
    seal = {
        "release_id": "test",
        "files": {"input": trial.sha(b"original tool")},
        "operating_input_sha256": {str(tmp_path / "operating"): trial.sha(b"original config")},
    }
    (tmp_path / "preflight-inputs.json").write_text(json.dumps(seal))
    if changed:
        path = tmp_path / ("work/source.py" if changed == "source" else changed)
        path.write_text("changed")
        with pytest.raises(RuntimeError, match="differs"):
            trial.checked()
    else:
        assert trial.checked()[1] == manifest


@pytest.mark.parametrize("difference", (None, "bytes", "dropin", "reload", "duplicate"))
def test_actual_installed_fragment_guard(difference, tmp_path, monkeypatch):
    actual, directory = tmp_path / "installed.service", tmp_path / "canonical"
    directory.mkdir()
    raw = (HERE / "deployment/marketdata-checkpoint-retirement.service").read_bytes()
    actual.write_bytes(raw if difference != "bytes" else raw + b"\n")
    (directory / safe.UNITS[0]).write_bytes(raw)
    fields = (
        f"FragmentPath={actual}\nDropInPaths={'extra.conf' if difference == 'dropin' else ''}\n"
        f"NeedDaemonReload={'yes' if difference == 'reload' else 'no'}\n"
    )
    if difference == "duplicate":
        fields += "NeedDaemonReload=no\n"
    monkeypatch.setattr(safe.clock, "command", lambda *a: fields)
    monkeypatch.setattr(safe.clock, "trusted", lambda path: Path(path))
    settings = SimpleNamespace(unit_directory=directory)
    if difference:
        with pytest.raises(Exception):
            safe.installed_service(settings, "md-t05-recurring-test.service", "run")
    else:
        safe.installed_service(settings, "md-t05-recurring-test.service", "run")


def test_projection_changes_code_and_units_only():
    projection = json.loads((HERE / "scope-projection.json").read_bytes())
    assert projection["changed_scope_fields"] == ["code", "system_units"]
    grant = json.loads((HERE / "projected-recurring.disabled.json").read_bytes())
    assert (
        not grant["enabled"]
        and grant["approved_at"] is None
        and grant["approval_reference"] is None
    )
    assert "scope_transition" not in grant


def test_trial_timer_has_next_occurrence_after_first_trigger():
    value = trial.timer_calendar(datetime(2026, 10, 18, 5, 29, 5, tzinfo=UTC))
    result = subprocess.run(
        [
            "/usr/bin/systemd-analyze",
            "calendar",
            "--iterations=2",
            "--base-time=2026-10-18 05:00:00 UTC",
            value,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "2026-10-18 05:29:05 UTC" in result.stdout
    assert "2026-10-19 05:29:05 UTC" in result.stdout


def test_unloaded_unit_is_not_a_successful_idle_inspection(monkeypatch):
    monkeypatch.setattr(
        trial,
        "show",
        lambda *a: {
            "LoadState": "not-found",
            "ActiveState": "inactive",
            "MainPID": "0",
            "Job": "",
        },
    )
    with pytest.raises(RuntimeError, match="disappeared"):
        trial.wait_idle("md-t05-recurring-test.service")


def test_timer_evidence_survives_failure_to_start_manual_trial(monkeypatch, tmp_path):
    timed = {"actual": {"scheduled": True}, "installed_guard_passed": True, "passed": True}
    monkeypatch.setattr(trial, "wait_result", lambda *a: timed)
    monkeypatch.setattr(trial, "wait_idle", lambda *a: {"LoadState": "loaded"})

    def missing(*args, **kwargs):
        raise RuntimeError("manual start failed")

    monkeypatch.setattr(trial, "command", missing)
    result = {}
    with pytest.raises(RuntimeError, match="manual start failed"):
        trial.check_invocations(tmp_path, "md-t05-recurring-test.service", result)
    assert result["timer"] == timed and result["first_unit"] == {"LoadState": "loaded"}
