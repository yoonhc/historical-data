import importlib.util
import json
import shutil
from dataclasses import replace
from types import SimpleNamespace

import pytest
from marketdata.domain import DataError, json_bytes, sha256

from scripts import checkpoint_code as code
from scripts import retire_checkpoints as retention


def copy_code(root):
    for name in code.SCRIPT_FILES + code.DECLARATIONS:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(code.CODE_ROOT / name, target)
    name = "packages/marketdata/src/marketdata"
    shutil.copytree(
        code.CODE_ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
    )


@pytest.mark.parametrize("change", ["helper", "core", "added", "lockfile"])
def test_source_identity_detects_every_execution_source_change(tmp_path, change):
    copy_code(tmp_path)
    first = code.describe_source(tmp_path)
    assert first["git_commit"] is None and first["source_dirty"] is None
    target = (
        tmp_path
        / {
            "helper": "scripts/archive_backups.py",
            "core": "packages/marketdata/src/marketdata/backup.py",
            "added": "packages/marketdata/src/marketdata/new_module.py",
            "lockfile": "uv.lock",
        }[change]
    )
    with target.open("a") as output:
        output.write("\n# A changed release\n")
    second = code.describe_source(tmp_path)
    assert first["source_sha256"] != second["source_sha256"]
    assert first["code_id"] != second["code_id"]
    (tmp_path / "new-evidence.txt").write_text("Reports do not change execution sources")
    assert code.describe_source(tmp_path) == second


def test_old_import_rejects_changed_files_on_disk(tmp_path):
    copy_code(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "isolated_code", tmp_path / "scripts/checkpoint_code.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.current_code() == module.STARTUP_CODE
    with (tmp_path / "scripts/retire_checkpoints.py").open("a") as output:
        output.write("\n# Changed after import\n")
    with pytest.raises(DataError, match="fresh Python process"):
        module.current_code()


def minimal_plan(tmp_path):
    cleaner = retention.Retirement.__new__(retention.Retirement)
    cleaner.root = tmp_path / "retention-state"
    cleaner.p = SimpleNamespace(identity={"repository_id": "a" * 64}, fingerprint="b" * 64)
    cleaner.policy = retention.Policy(tmp_path / "profile", tmp_path / "proof", "c" * 64)
    return cleaner, {
        "schema": 2,
        "identity": cleaner.p.identity,
        "profile_sha256": cleaner.p.fingerprint,
        "policy_sha256": cleaner.policy.fingerprint,
        "code": code.current_code(),
    }


def write_plan(cleaner, plan):
    payload = {k: v for k, v in plan.items() if k != "plan_id"}
    pid = sha256(json_bytes(payload))
    retention.persist(cleaner.root / "plans" / f"{pid}.json", {**payload, "plan_id": pid})
    return pid


def test_approved_plan_rejects_code_policy_and_legacy_changes(tmp_path):
    cleaner, plan = minimal_plan(tmp_path)
    pid = write_plan(cleaner, plan)
    assert cleaner.load_plan(pid)["code"] == code.current_code()
    cleaner.policy = replace(cleaner.policy, apply_enabled=True)
    with pytest.raises(ValueError, match="Policy changed"):
        cleaner.load_plan(pid)
    cleaner.policy = replace(cleaner.policy, apply_enabled=False)
    changed = json.loads(json_bytes(plan))
    changed["code"]["source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="Approved code identity differs"):
        cleaner.load_plan(write_plan(cleaner, changed))
    legacy = {k: v for k, v in plan.items() if k != "code"}
    legacy["schema"] = 1
    with pytest.raises(ValueError, match="Legacy plan"):
        cleaner.load_plan(write_plan(cleaner, legacy))
    assert cleaner.load_plan(write_plan(cleaner, legacy), current_config=False)["schema"] == 1


@pytest.mark.parametrize("selected", ["keep", "candidate"])
def test_snapshot_association_is_pinned_even_if_other_snapshot_has_same_backup(selected):
    cleaner = retention.Retirement.__new__(retention.Retirement)
    entry = {"backup_id": "a" * 64, "path": "/backup/day/copy", "source_root": "/original"}
    cleaner.a = SimpleNamespace(
        state={
            "checkpoints": {
                entry["backup_id"]: {"entry": entry, "stored": {"snapshot_id": "b" * 64}}
            }
        }
    )
    plan = {"keep": [], "candidates": []}
    if selected == "keep":
        plan["keep"] = [
            {**entry, "snapshot_id": "b" * 64, "age_seconds": 100, "protection": ["latest"]}
        ]
    else:
        plan["candidates"] = [{"entry": entry, "snapshot_id": "b" * 64}]
    cleaner.check_bindings(plan)
    cleaner.a.state["checkpoints"][entry["backup_id"]]["stored"]["snapshot_id"] = "c" * 64
    with pytest.raises(ValueError, match="association changed"):
        cleaner.check_bindings(plan)


def test_space_delta_is_observation_and_failure_keeps_it_unknown(monkeypatch):
    before = {"device": 1, "available_bytes": 500, "free_bytes": 600}
    observation = {"before": before, "after": None, "exact_reclaimed_bytes": None}
    monkeypatch.setattr(
        retention,
        "filesystem_sample",
        lambda _: {
            "device": 1,
            "available_bytes": 400,
            "free_bytes": 450,
        },
    )
    retention.finish_space_observation(observation, None)
    assert observation["observed_available_change_bytes"] == -100
    assert observation["observed_free_change_bytes"] == -150
    assert observation["exact_reclaimed_bytes"] is None
    interrupted = {"before": before, "after": None, "observed_available_change_bytes": None}
    monkeypatch.setattr(
        retention, "filesystem_sample", lambda _: (_ for _ in ()).throw(OSError("unmounted"))
    )
    retention.finish_space_observation(interrupted, None)
    assert interrupted["after"]["ok"] is False
    assert interrupted["observed_available_change_bytes"] is None
