"""Read the scheduled checkpoint/archive receipts and verify repaired file inclusion.

Does not create a checkpoint, run restic backup/check/restore, or alter archive state.
The only restic reads are snapshots and dump of the named snapshot's backup.json.
"""

import hashlib
import json
import sys
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from marketdata.backup import open_catalog
from marketdata.checkpoint_locks import checkpoint_locks
from marketdata.fileio import packed_record, read_packed, safe_file

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from scripts.archive_backups import (  # noqa: E402
    Archive,
    Busy,
    Profile,
    checkpoint,
    repository_lock,
)

TARGET_COLLECTION = "852ddace89cc418bb1b2e1aaa839519b"
TARGET_EVIDENCE = (
    ROOT
    / "reports/baseline/aggtrade-gap-repair-2026-10-09/20261009T000554Z-ab5276dd/verified-files.json"
)
MIN_CHECKPOINT = datetime.fromisoformat("2026-10-09T04:10:00+00:00")


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Pending(Exception):
    pass


def main():
    stamp = datetime.now(timezone.utc)
    output = HERE / "backup-observations" / stamp.strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "observed_at": stamp.isoformat(),
        "status": "pending",
        "collection_id": TARGET_COLLECTION,
        "new_full_check": False,
        "new_checkpoint_or_archive_or_restore": False,
    }
    try:
        daily_path = ROOT / "state/maintenance-daily-latest.json"
        daily = json.loads(daily_path.read_text())
        (output / "maintenance-daily.json").write_text(json.dumps(daily, indent=2) + "\n")
        report["daily_started_at"] = daily["started_at"]
        report["daily_phase"] = daily["phase"]
        if datetime.fromisoformat(daily["started_at"]) < MIN_CHECKPOINT:
            raise Pending("The 13:10 KST checkpoint job has not produced a new daily report yet")
        if not daily.get("ok") or not daily.get("backup", {}).get("ok"):
            if daily.get("phase") in {"failed", "error"}:
                raise RuntimeError("Scheduled checkpoint failed; inspect the preserved report")
            raise Pending("New checkpoint/validation not complete")
        assert daily["validation"]["ok"] and not daily["validation"]["failures"]
        profile = Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
        profile.guard()
        with repository_lock(profile):
            before = sha(profile.state_file)
            archive = Archive(profile, output / "restic-reads")
            matches = [
                r
                for r in archive.state["checkpoints"].values()
                if r["entry"]["backup_id"] == daily["backup"]["backup_id"]
            ]
            if len(matches) != 1 or not matches[0].get("stored"):
                raise Pending("New daily backup ID has not been stored by the scheduled archiver")
            item = matches[0]
            entry, stored = item["entry"], item["stored"]
            report.update(checkpoint=entry, stored=stored)
            assert datetime.fromisoformat(entry["checkpoint_at"]) >= MIN_CHECKPOINT
            snapshot_id = stored["snapshot_id"]
            check = archive.state["repository_check"]
            if (
                not check
                or not check.get("ok")
                or not check.get("read_data")
                or check.get("status") != "complete"
            ):
                raise Pending("Repository full data check not complete")
            if snapshot_id not in check["snapshot_ids"] or datetime.fromisoformat(
                check["started_at"]
            ) < datetime.fromisoformat(stored["archive_finished_at"]):
                raise Pending("Completed full check does not cover the new snapshot")
            receipt = check["receipt"]
            assert receipt["arguments"] == ["check", "--read-data"] and receipt["returncode"] == 0
            receipt_path = Path(receipt["stdout"]).with_suffix(".json")
            assert json.loads(receipt_path.read_text()) == receipt
            scheduled = json.loads((receipt_path.parent / "run.json").read_text())
            assert scheduled["ok"] and scheduled["result"]["processed"] > 0
            assert snapshot_id in scheduled["result"]["stored_snapshots"]
            report["scheduled_run"] = scheduled
            verified_inputs = []
            for path in receipt_path.parent.glob("*-verify-input.json"):
                proof = json.loads(path.read_text())
                if proof.get("result", {}).get("backup_id") == entry["backup_id"]:
                    assert proof["result"]["ok"]
                    verified_inputs.append({"path": str(path), "sha256": sha(path), "proof": proof})
            assert len(verified_inputs) == 1
            report["archive_input_verification"] = verified_inputs
            report["full_check"] = check
            report["full_check_evidence"] = [
                {"path": str(p), "sha256": sha(p)}
                for p in [receipt_path, Path(receipt["stdout"]), Path(receipt["stderr"])]
            ]
            live = archive.restic.snapshots()
            snapshot = next(s for s in live if s["id"] == snapshot_id)
            report["snapshot"] = snapshot
            report["matched_attempt_id"] = archive.match(snapshot, entry)
            cp = Path(entry["path"])
            with checkpoint_locks([cp], wait=False):
                assert checkpoint(cp, profile) == entry
                manifest_bytes = (cp / "backup.json").read_bytes()
                manifest = json.loads(manifest_bytes)
                physical = {f["path"]: f for f in manifest["files"]}
                assert (
                    sha(cp / "state/catalog.sqlite") == physical["state/catalog.sqlite"]["sha256"]
                )
                targets = json.loads(TARGET_EVIDENCE.read_text())
                verified = []
                with closing(open_catalog(cp / "state/catalog.sqlite")) as db:
                    collection = db.execute(
                        "SELECT * FROM event_collections WHERE id=?", (TARGET_COLLECTION,)
                    ).fetchone()
                    assert collection is not None
                    report["collection"] = dict(collection)
                    records = [
                        dict(v)
                        for v in db.execute(
                            "SELECT * FROM event_segments WHERE collection_id=?",
                            (TARGET_COLLECTION,),
                        )
                    ]
                    assert len(records) == len(targets) == 265
                    by_id = {v["id"]: v for v in records}
                    for t in targets:
                        row = by_id[t["id"]]
                        assert all(row[k] == t[k] for k in row)
                        artifact = db.execute(
                            "SELECT * FROM artifacts WHERE id=?", (row["source_id"],)
                        ).fetchone()
                        assert artifact is not None
                        for kind, path, digest in [
                            ("canonical", row["path"], row["file_sha"]),
                            ("raw", artifact["path"], artifact["id"]),
                        ]:
                            packed = packed_record(db, path)
                            blob = (
                                read_packed(cp, packed)
                                if packed
                                else safe_file(cp, path).read_bytes()
                            )
                            assert hashlib.sha256(blob).hexdigest() == digest
                            physical_path = packed["pack_path"] if packed else path
                            assert physical_path in physical
                            verified.append(
                                {
                                    "kind": kind,
                                    "path": path,
                                    "sha256": digest,
                                    "physical_path": physical_path,
                                    "physical_sha256": physical[physical_path]["sha256"],
                                }
                            )
                assert len(verified) == 530 and sum(v["row_count"] for v in records) == 264070
                raw, read_receipt = archive.restic.command(["dump", snapshot_id, "/backup.json"])
                assert raw == manifest_bytes
                report["snapshot_manifest_receipt"] = read_receipt
                (output / "verified-inclusion.json").write_text(
                    json.dumps(verified, indent=2) + "\n"
                )
                report["inclusion"] = {
                    "raw_files": 265,
                    "canonical_files": 265,
                    "rows": 264070,
                    "repaired_ids": 264069,
                    "boundary_row_included": True,
                    "source_evidence_sha256": sha(TARGET_EVIDENCE),
                    "checkpoint_logical_hashes_verified": True,
                    "named_snapshot_manifest_bytes_equal": True,
                    "snapshot_data_coverage": "existing scheduled --read-data receipt; no new extraction of 530 files",
                }
            assert sha(profile.state_file) == before
            report["archive_state_sha256_unchanged"] = before
        report["status"] = "complete"
    except (Pending, Busy) as exc:
        report["pending_reason"] = str(exc)
    except Exception as exc:
        report.update(status="error", error=repr(exc), traceback=traceback.format_exc())
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (output / "result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                k: report.get(k)
                for k in ("status", "pending_reason", "error", "checkpoint", "inclusion")
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
