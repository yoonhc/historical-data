"""Verify the assembled development tree, without using operating imports/state."""

import hashlib
import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from marketdata import cli, request_contract

from scripts import render_retirement_units, render_scheduled_units, run_job

ROOT = Path(__file__).resolve().parents[1]


def test_business_modules_and_entrypoints_come_from_the_unified_tree():
    modules = (
        "marketdata.clock_evidence",
        "marketdata.job_clock",
        "marketdata.request_contract",
        "marketdata.daily_schedule",
        "marketdata.daily_review",
        "marketdata.daily_probe",
        "marketdata.checkpoint_clock",
        "marketdata.checkpoint_evidence",
        "marketdata.backup",
        "marketdata.maintenance",
        "scripts.clock_retirement",
        "scripts.checkpoint_evidence",
        "scripts.restic_schedule",
        "scripts.archive_backups",
        "scripts.archive_output",
    )
    for name in modules:
        source = Path(importlib.import_module(name).__file__).resolve()
        assert source.is_relative_to(ROOT)
        assert not source.is_relative_to(ROOT / "reports")
        assert not source.is_relative_to(ROOT / ".venv")
    run_job.use_source_tree()
    assert (
        Path(run_job.load_contract().__file__)
        == ROOT / "packages/marketdata/src/marketdata/request_contract.py"
    )


def test_original_observation_provider_is_still_pinned():
    source = ROOT / "packages/marketdata/src/marketdata/checkpoint_clock.py"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == run_job.OBSERVER_SHA256


def test_cli_has_collector_evidence_alongside_scheduled_jobs():
    p = cli.parser()
    assert (
        p.parse_args(["clock-evidence", "--collection-id", "example"]).command == "clock-evidence"
    )
    assert p.parse_args(["maintenance", "--mode", "hourly"]).command == "maintenance"
    assert p.parse_args(["oi-quality"]).command == "oi-quality"


@pytest.fixture
def source_copy(tmp_path):
    tree = tmp_path / "export"
    for name in ("packages/marketdata/src", "scripts", "integrations/runtime"):
        shutil.copytree(ROOT / name, tree / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "packages/marketdata/pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, tree / name)
    binding = tmp_path / "binding.json"
    binding.write_text("{}")
    return tree, binding


@pytest.mark.parametrize(
    "name",
    [
        "packages/marketdata/src/marketdata/maintenance.py",
        "packages/marketdata/src/marketdata/clock_evidence.py",
        "scripts/archive_output.py",
        "scripts/run_job.py",
        "uv.lock",
    ],
)
def test_request_identity_covers_changes_within_the_same_checkout(source_copy, name):
    tree, binding = source_copy
    before = request_contract.inputs("pack", [], binding, tree)
    target = tree / name
    target.write_bytes(target.read_bytes() + b"\n# different reviewed source\n")
    after = request_contract.inputs("pack", [], binding, tree)
    assert request_contract.digest(request_contract.encode(before)) != request_contract.digest(
        request_contract.encode(after)
    )


def test_export_does_not_silently_import_installed_old_package(source_copy):
    tree, _ = source_copy
    code = """
import importlib.util, sys
from pathlib import Path
path = Path(sys.argv[1]) / 'scripts/run_job.py'
spec = importlib.util.spec_from_file_location('export_runner', path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.use_source_tree()
from marketdata import clock_evidence, daily_schedule, job_clock
for module in (clock_evidence, daily_schedule, job_clock, runner.load_contract()):
    assert Path(module.__file__).is_relative_to(Path(sys.argv[1]))
print('unified-export')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-I", "-c", code, str(tree)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "unified-export"


def rendered():
    return render_scheduled_units.render(
        release="/opt/reviewed",
        store_root="/srv/marketdata",
        user="marketdata",
        user_home="/home/marketdata",
    )


def test_system_jobs_keep_timer_bytes_and_use_only_unified_waiter_and_wrapper():
    units = rendered()
    assert len(units) == 17
    records = json.loads(
        (ROOT / "reports/baseline/source-integration-2026-10-25/origins.json").read_text()
    )["files"]
    timer_hashes = {
        Path(r["target"]).name: r["source_sha256"]
        for r in records
        if r["target"].startswith("integrations/systemd/scheduled/")
        and r["target"].endswith(".timer")
    }
    for job in render_scheduled_units.JOBS:
        name = f"marketdata-{job}"
        service = units[name + ".service"]
        assert "Requires=marketdata-jobs-clock-ready.service" in service
        assert "ExecStartPre=" not in service
        assert "ProtectClock=yes" in service and "User=marketdata" in service
        assert f"/opt/reviewed/scripts/run_job.py --job {job} " in service
        assert "--run --" in service
        assert "/candidate/" not in service and "/policy/run_job.py" not in service
        assert (
            hashlib.sha256(units[name + ".timer"].encode()).hexdigest()
            == timer_hashes[name + ".timer"]
        )
    assert "--wait" in units["marketdata-jobs-clock-ready.service"]


@pytest.mark.parametrize("path", ["relative", "/opt/../wrong", "/opt/has space", "/opt/$ENV"])
def test_scheduled_renderer_refuses_ambiguous_paths(path):
    with pytest.raises(ValueError):
        render_scheduled_units.render(
            release=path, store_root="/srv/data", user="marketdata", user_home="/home/marketdata"
        )


def test_retirement_units_keep_one_mount_for_source_and_staging():
    profile = dict(
        store_root="/srv/data",
        backup_mount="/mnt/backup",
        backup_dir="/mnt/backup/marketdata/automatic",
        state_root="/srv/archive-state",
        expected_repository_id="a" * 64,
        cache_root="/srv/cache",
        restic_repository="/mnt/backup/restic",
    )
    units = render_retirement_units.render(
        "/opt/reviewed", "/etc/md/settings.toml", "/var/lib/md", profile
    )
    for name in (
        "marketdata-checkpoint-retirement.service",
        "marketdata-checkpoint-retirement-apply@.service",
    ):
        assert "ReadWritePaths=/mnt/backup/marketdata\n" in units[name]
        assert "ReadWritePaths=/mnt/backup/marketdata/automatic" not in units[name]
        assert ".automatic.retiring" not in units[name]
        assert "Restart=no" in units[name]
    assert (
        "ReadWritePaths=/mnt/backup/marketdata"
        not in units["marketdata-checkpoint-retirement-preview.service"]
    )
    assert "Persistent=false" in units["marketdata-checkpoint-retirement.timer"]


def test_system_bootstrap_renderer_keeps_inventory_guard_in_unified_runtime():
    path = ROOT / "integrations/runtime/render_bootstrap.py"
    spec = importlib.util.spec_from_file_location("unified_bootstrap_renderer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = json.loads((ROOT / "integrations/runtime/profile.example.json").read_text())
    inventory = json.loads((ROOT / "integrations/runtime/user-unit-inventory.json").read_text())
    topology = {
        "schema": 1,
        "purpose": "scheduled-manager-only-v1",
        "units": {},
        "system_inventory": [],
    }
    files, profile = module.render(
        runtime, json.dumps(runtime).encode(), inventory, system_topology=topology
    )
    assert (
        "/integrations/runtime/bootstrap_system.py bootstrap"
        in files["system/marketdata-clock-bootstrap.service"]
    )
    assert (
        profile["guard_files"]["/etc/marketdata/scheduled-system/topology.json"]
        == hashlib.sha256(files["scheduled-system/topology.json"].encode()).hexdigest()
    )
