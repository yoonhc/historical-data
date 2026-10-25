import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from marketdata import backup
from marketdata import checkpoint_evidence as evidence
from marketdata.domain import json_bytes, sha256
from test_checkpoint_evidence import lab as lab  # noqa: F401

from scripts import archive_backups as archive
from scripts import checkpoint_evidence as registration
from scripts.clock_retirement import Settings


def test_registration_is_explicit_readonly_preview_and_repeated_first_witness(
    lab, tmp_path, monkeypatch
):
    store, original_reg, _, observe = lab
    point = backup.create_backup(store, tmp_path / "old" / "point", min_free_gib=0)
    p = SimpleNamespace(identity=original_reg.scope, store_root=store.root, fingerprint="b" * 64)
    entry = archive.checkpoint(tmp_path / "old/point", p)
    entries = [entry]
    state = {
        "checkpoints": {
            point["backup_id"]: {
                "entry": entry,
                "input_verified_at": "verified",
                "stored": {"snapshot_id": "c" * 64},
            }
        },
        "repository_check": {"snapshot_ids": ["c" * 64]},
    }
    settings = Settings(
        *(
            tmp_path / name
            for name in ("policy", "recurring", "manual", "binding", "control", "units")
        )
    )

    @contextmanager
    def inputs(_):
        yield entries, state

    monkeypatch.setattr(registration, "profile", lambda _: p)
    monkeypatch.setattr(registration, "inputs", inputs)
    before = json_bytes(state)
    preview = registration.inventory(settings)
    assert not settings.state_root.exists() and json_bytes(state) == before
    approval = {
        "purpose": "checkpoint-evidence-registration-v1",
        "enabled": True,
        "action": "register",
        "plan_id": preview["plan_id"],
        "approval_reference": "isolated registration",
    }
    with pytest.raises(ValueError, match="approval"):
        registration.register(settings, preview, {**approval, "action": "run"}, observer=observe)
    assert not settings.state_root.exists()
    result = registration.register(settings, preview, approval, observer=observe)
    assert result["registered"] and not result["deletion_authority"]
    reg = registration.registry(settings, p)
    original = {
        path.name: sha256(path.read_bytes()) for path in (reg.root / "objects").glob("*.json")
    }

    def no_new_query():
        raise AssertionError("Existing valid witness must be reused")

    repeated = registration.register(settings, preview, approval, observer=no_new_query)
    assert repeated["results"] == result["results"]
    assert original == {
        path.name: sha256(path.read_bytes()) for path in (reg.root / "objects").glob("*.json")
    }
    assert json_bytes(state) == before
    # A changed completed inventory cannot reuse the old plan or silently
    # replace the established baseline. No SQL, manifest or timestamp editing.
    entries.clear()
    with pytest.raises(ValueError, match="Inventory changed"):
        registration.register(settings, preview, approval, observer=observe)
    assert len(reg.graph().baselines) == 1
    assert (
        json.loads((tmp_path / "old/point/backup.json").read_bytes())["backup_id"]
        == point["backup_id"]
    )


def test_registration_missing_clock_does_not_claim_completion_proof(lab, tmp_path, monkeypatch):
    # This exercises the shared first-witness operation directly after an
    # already approved baseline; failed queries do not publish valid witnesses.
    store, reg, _, observe = lab
    result = backup.create_backup(store, tmp_path / "old", min_free_gib=0)
    ident = evidence.identity(result["path"], backup.load_backup(result["path"])[1])
    with reg.lock():
        with pytest.raises(OSError):
            reg.observe(ident, lambda: (_ for _ in ()).throw(OSError("unavailable")))
        assert reg.graph().proof(ident) is None
        reg.observe(ident, observe)
    assert reg.graph().proof(ident) is not None
