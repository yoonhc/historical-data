import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t18_preflight", HERE / "system_preflight.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
c = p.c
PLAN = json.loads((HERE / "prepared-plan.json").read_bytes())


def test_namespace_overlay_contains_only_proposed_files_and_disabled_authority(tmp_path):
    original = {path: Path(path).read_bytes() for path in PLAN["changes"]}
    trial, state = tmp_path / "trial", tmp_path / "state"
    text = p.unit(
        PLAN["changes"][c.SERVICE_PATH]["before"]["text"],
        trial,
        state,
        "after",
        "md-t18-test-after.service",
        "md-t18-test-before.timer",
        PLAN,
    )
    assert "Requires=" not in text and "After=" not in text
    assert text.count("ReadWritePaths=") == 1
    assert f"ReadWritePaths={state}\n" in text
    targets = {
        line.split("=", 1)[1].split(":", 1)[1]
        for line in text.splitlines()
        if line.startswith("BindReadOnlyPaths=")
    }
    assert targets == {*PLAN["changes"], c.LEDGER, c.GRANT}
    grant = json.loads((trial / "shadow/grant").read_bytes())
    assert (
        grant["enabled"] is False
        and grant["approved_at"] is None
        and grant["approval_reference"] is None
    )
    ledger = json.loads((trial / "shadow/ledger").read_bytes())
    assert {k: v for k, v in ledger.items() if k != "profile_sha256"} == {
        k: v for k, v in PLAN["ledger_before"].items() if k != "profile_sha256"
    }
    assert original == {path: Path(path).read_bytes() for path in original}


def test_actual_protection_and_interpreter_are_retained_in_trial(tmp_path):
    template = PLAN["changes"][c.SERVICE_PATH]["before"]["text"]
    text = p.unit(
        template,
        tmp_path / "trial",
        tmp_path / "state",
        "before",
        "md-t18-test.service",
        "md-t18-test.timer",
        PLAN,
    )
    for line in template.splitlines():
        if line.startswith(
            (
                "User=",
                "Group=",
                "UMask=",
                "Protect",
                "Private",
                "NoNew",
                "Environment",
                "Kill",
                "Restart=",
            )
        ):
            assert line in text
    assert text.count("ExecStart=") == 1 and f"ExecStart={PLAN['python']} -B -I " in text
    assert "BindReadOnlyPaths=" not in text


def test_missing_trial_units_are_accepted_only_as_complete_not_found_output(monkeypatch):
    name = "md-t18-test.service"
    monkeypatch.setattr(
        p,
        "command",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout=f"Id={name}\nLoadState=not-found\nActiveState=inactive\n"
        ),
    )
    assert p.show(name)[name]["LoadState"] == "not-found"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "Id=md-t18-other.service\nLoadState=not-found\n",
        "Id=md-t18-test.service\nLoadState=loaded\n",
    ],
)
def test_failed_manager_read_never_becomes_an_idle_trial(monkeypatch, raw):
    monkeypatch.setattr(p, "command", lambda *a, **k: SimpleNamespace(returncode=1, stdout=raw))
    with pytest.raises(RuntimeError):
        p.show("md-t18-test.service")


def test_only_clock_binding_changes_in_proposed_t05_scope():
    before = json.loads(PLAN["recurring_before"]["text"])
    after = PLAN["recurring_proposal_after"]
    old = dict(before["scope"])
    new = dict(after["scope"])
    old.pop("clock_binding")
    new.pop("clock_binding")
    assert old == new
    assert before["scope_id"] != after["scope_id"]
    assert before["enabled"] is True and after["enabled"] is False
    assert after["scope_id"] == c.sha(c.canonical(after["scope"]))


def test_guard_connection_changes_only_two_registered_files():
    before = json.loads(PLAN["changes"][c.PROFILE]["before"]["text"])
    after = json.loads(PLAN["changes"][c.PROFILE]["after"]["text"])
    assert {k: v for k, v in before.items() if k != "guard_files"} == {
        k: v for k, v in after.items() if k != "guard_files"
    }
    assert {
        k for k in before["guard_files"] if before["guard_files"][k] != after["guard_files"][k]
    } == {c.SERVICE_PATH, c.TOPOLOGY}
    topology_before = json.loads(PLAN["changes"][c.TOPOLOGY]["before"]["text"])
    topology_after = json.loads(PLAN["changes"][c.TOPOLOGY]["after"]["text"])
    topology_before["units"][c.SERVICE]["ExecStart"] = topology_after["units"][c.SERVICE][
        "ExecStart"
    ]
    assert topology_before == topology_after


def test_source_release_uses_existing_interpreter_and_only_two_modified_modules():
    assert PLAN["python"] == str(c.PYTHON)
    assert not any(".venv" in Path(name).parts for name in PLAN["release_entries"])
    changed = []
    for name, item in PLAN["release_entries"].items():
        assert c.file_sha(item["source"]) == item["sha256"]
        if name == "parents.json":
            continue
        old = c.BASE / name
        if not old.exists() or c.file_sha(old) != item["sha256"]:
            changed.append(name)
    assert sorted(changed) == [
        "preserve/scripts/archive_backups.py",
        "preserve/scripts/archive_output.py",
    ]


def test_receipt_time_and_body_metadata_are_distinct_in_growth_measurement():
    cap = PLAN["capacity"]
    assert len(cap["manifest_outputs"]) == 24
    assert cap["source_run"] == "0597c07e9ae643d389ef3b69a0b737fd"
    assert (
        cap["available_bytes"]
        > cap["required_collection_reserve_bytes"]
        + cap["first_body_bytes"]
        + cap["largest_temporary_stdout_bytes"]
        + cap["release_bytes"]
    )
    assert len(PLAN["pending_checkpoints_observed"]) == 2
    assert all("mtime" not in item for item in cap["manifest_outputs"].values())
