"""Read existing validation evidence and live snapshot metadata; never run backup/check/restore."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sys
import traceback
import zipfile

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROOT = Path("/home/yoonhc/projects/algoTrade/historical_data")
sys.path.insert(0, str(ROOT))
from scripts.archive_backups import Archive, Profile, checkpoint, load_state, overview, repository_lock
from marketdata.checkpoint_locks import checkpoint_locks
from marketdata.backup import open_catalog
from marketdata.fileio import packed_record, read_packed, safe_file


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def verify_previous_update(cp, manifest, archive, snapshot_id):
    """Read only four prior revisions under the caller's shared checkpoint lock."""
    source = ROOT / "reports/baseline/system-cutover-readiness-2026-10-07/followup-validation.json"
    targets = json.loads(source.read_text())["resolved"]
    physical = {r["path"]: r for r in manifest["files"]}
    fetched = {}
    results = []
    with closing(open_catalog(cp / "state/catalog.sqlite")) as db:
        for target in targets:
            row = db.execute("SELECT * FROM revisions WHERE id=?", (target["revision"],)).fetchone()
            assert row is not None and row["day"] == "2026-10-06"
            assert row["row_count"] == target["rows"]
            active = db.execute("SELECT revision_id FROM active WHERE key_id=? AND day=?",
                                (row["key_id"], row["day"])).fetchone()
            assert active and active[0] == row["id"]
            packed = packed_record(db, row["path"])
            blob = read_packed(cp, packed) if packed else safe_file(cp, row["path"]).read_bytes()
            assert hashlib.sha256(blob).hexdigest() == row["file_sha"]
            relative = packed["pack_path"] if packed else row["path"]
            assert relative in physical
            if relative not in fetched:
                raw, receipt = archive.restic.command(["dump", snapshot_id, "/" + relative])
                assert hashlib.sha256(raw).hexdigest() == physical[relative]["sha256"]
                fetched[relative] = (raw, receipt)
            raw, receipt = fetched[relative]
            if packed:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    restored = z.read(row["path"])
            else:
                restored = raw
            assert restored == blob
            table = pq.ParquetFile(pa.BufferReader(blob)).read()
            assert table.num_rows == target["rows"]
            result = {"key": target["key"], "revision_id": row["id"], "day": row["day"],
                      "row_count": table.num_rows, "path": row["path"], "physical_path": relative,
                      "sha256": row["file_sha"], "active_in_checkpoint": True,
                      "snapshot_bytes_match": True, "dump_receipt": receipt}
            if "timestamp" in table.column_names:
                times = table["timestamp"].to_pylist()
                result.update(first_timestamp=str(min(times)), last_timestamp=str(max(times)))
            results.append(result)
    assert len(results) == 4 and sum(r["row_count"] for r in results) == 4608
    return {"source_evidence": str(source), "source_evidence_sha256": sha(source),
            "passed": True, "rows": 4608, "revisions": results,
            "scope": "selected revision bytes and checkpoint catalog; not a full restore drill"}


def main():
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "new_full_integrity_check": False, "new_backup_or_restore": False}
    try:
        profile = Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
        report["overview"] = overview(profile)
        assert report["overview"]["healthy"] and report["overview"]["pending_count"] == 0
        assert not report["overview"]["incomplete_folders"]
        profile.guard()
        with repository_lock(profile):
            before = sha(profile.state_file)
            archive = Archive(profile, HERE / "restic-commands")
            latest = max((r for r in archive.state["checkpoints"].values() if r["stored"]), key=lambda r: r["entry"]["checkpoint_at"])
            entry = latest["entry"]
            snapshot_id = latest["stored"]["snapshot_id"]
            cp = Path(entry["path"])
            with checkpoint_locks([cp], wait=False):
                assert checkpoint(cp, profile) == entry
                report["checkpoint"] = entry
                report["stored"] = latest["stored"]
                report["input_verified_at"] = latest["input_verified_at"]
                daily = json.loads((HERE / "maintenance-daily-latest.json").read_text())
                assert daily["ok"] and daily["validation"]["ok"] and not daily["validation"]["failures"]
                assert daily["backup"]["backup_id"] == entry["backup_id"] and daily["backup"]["ok"]
                manifest = json.loads((cp / "backup.json").read_text())
                catalog = next(x for x in manifest["files"] if x["path"] == "state/catalog.sqlite")
                report["catalog_current_sha256"] = sha(cp / catalog["path"])
                assert report["catalog_current_sha256"] == catalog["sha256"]
                report["restic_version"] = archive.restic.identify()
                live = archive.restic.snapshots()
                live_ids = {s["id"] for s in live}
                recorded_ids = {r["stored"]["snapshot_id"] for r in archive.state["checkpoints"].values() if r["stored"]}
                assert live_ids == recorded_ids
                matches = [s for s in live if s["id"] == snapshot_id]
                assert len(matches) == 1
                report["snapshot"] = matches[0]
                report["matched_attempt_id"] = archive.match(matches[0], entry)
                report["previous_update_inclusion"] = verify_previous_update(cp, manifest, archive, snapshot_id)
                report["live_snapshot_ids"] = sorted(live_ids)
                check = archive.state["repository_check"]
                assert check["ok"] and check["read_data"] and check["status"] == "complete"
                assert set(check["snapshot_ids"]) == live_ids
                assert check["receipt"]["arguments"] == ["check", "--read-data"]
                assert check["receipt"]["returncode"] == 0
                assert datetime.fromisoformat(latest["stored"]["archive_finished_at"]) <= datetime.fromisoformat(check["started_at"])
                receipt_path = Path(check["receipt"]["stdout"]).with_suffix(".json")
                assert json.loads(receipt_path.read_text()) == check["receipt"]
                evidence_paths = [receipt_path, Path(check["receipt"]["stdout"]), Path(check["receipt"]["stderr"])]
                reports_dir = receipt_path.parent
                inputs = []
                for f in reports_dir.glob("*-verify-input.json"):
                    proof = json.loads(f.read_text())
                    if proof.get("result", {}).get("backup_id") == entry["backup_id"]:
                        assert proof["result"]["ok"]
                        inputs.append({"path": str(f), "sha256": sha(f), "proof": proof})
                assert len(inputs) == 1
                scheduled_run = json.loads((reports_dir / "run.json").read_text())
                assert scheduled_run["ok"] and scheduled_run["result"]["processed"] == 1
                assert snapshot_id in scheduled_run["result"]["stored_snapshots"]
                report["scheduled_archive_run"] = scheduled_run
                report["input_verification_evidence"] = inputs
                report["repository_full_check"] = check
                report["check_evidence_files"] = [{"path": str(f), "sha256": sha(f), "bytes": f.stat().st_size} for f in evidence_paths]
                report["check_stdout"] = Path(check["receipt"]["stdout"]).read_text()
                report["check_stderr"] = Path(check["receipt"]["stderr"]).read_text()
            report["archive_state_sha256_before"] = before
            report["archive_state_sha256_after"] = sha(profile.state_file)
            assert report["archive_state_sha256_before"] == report["archive_state_sha256_after"]
        report["passed"] = True
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (HERE / "backup-readiness.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k:report.get(k) for k in ("passed", "error", "checkpoint", "stored", "restic_version", "archive_state_sha256_before", "archive_state_sha256_after")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
