"""Check legacy replay and the six observed failed epochs with the proposed policy.

Run from the production repository with PYTHONPATH pointing to the staged package.
Reads immutable production inputs; writes only this evidence directory.
Scenarios retain actual snapshot receipt order and original epoch boundaries.
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

from marketdata import depth, depth_stream
from marketdata.domain import json_bytes, now, sha256
from marketdata.store import Store

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent


def save(name, value):
    (OUT / name).write_text(json.dumps(json.loads(json_bytes(value)), indent=2) + "\n")


class Reader:
    _revision_table = Store._revision_table
    file_bytes = Store.file_bytes
    raw = Store.raw

    def __init__(self):
        self.root = ROOT
        self.db = sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self._files_db = self.db


def main():
    reader = Reader()
    source = ROOT / "reports/baseline/depth-coverage-2026-09-25/analyze.py"
    spec = importlib.util.spec_from_file_location("coverage_inputs", source)
    coverage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coverage)
    try:
        replayed = []
        for identity in (
            "5039a296a6164759a0c5fe9ae9c21390",
            "ed201401a9d0445f995aa12f3a8f3c49",
            "b17f5ce3e25a4ea9a59c6924fcf3a356",
        ):
            result = depth.replay(reader, identity)
            assert result["replay_matches_live"]
            replayed.append({k: v for k, v in result.items() if k not in ("epochs", "segments")})
            print(json_bytes(replayed[-1]).decode(), flush=True)
        save("legacy-replays.json", replayed)

        cases = []
        for identity, epoch_id, expected_resolved in (
            ("ee35f61e7f2d4cf082a74f0035962c31", 0, True),
            ("1f01b44a839c4cbebe4b3fdd385d382a", 1, False),
            ("5039a296a6164759a0c5fe9ae9c21390", 2, True),
            ("ed201401a9d0445f995aa12f3a8f3c49", 3, False),
            ("ed201401a9d0445f995aa12f3a8f3c49", 4, True),
            ("ed201401a9d0445f995aa12f3a8f3c49", 5, True),
        ):
            original = reader.db.execute(
                "SELECT report FROM event_collections WHERE id=?", (identity,)
            ).fetchone()[0]
            report = json.loads(original)
            epoch = next(e for e in report["epochs"] if e["epoch"] == epoch_id)
            rows, inputs, sources = coverage.load_rows(reader.db, report, epoch)
            legacy = depth.new_session(report, epoch["reason"])
            failure = None
            for row in rows:
                if legacy.error:
                    assert isinstance(failure, depth.SnapshotBehind)
                    continue
                try:
                    legacy.take(row)
                except depth.SyncLost as exc:
                    failure, legacy.error = exc, str(exc)
            assert json_bytes(legacy.summary()) == json_bytes(epoch["final"])
            policy_report = {
                **report,
                "history_policy": depth.HISTORY_POLICY,
                "history_limits": dict(depth.HISTORY_LIMITS),
            }
            previous, seed_rows, seed_inputs = None, [], []
            if epoch["reason"] in depth.SAME_SOCKET_REASONS:
                prior_epoch = next(e for e in report["epochs"] if e["epoch"] == epoch_id - 1)
                seed_rows, seed_inputs, _ = coverage.load_rows(reader.db, report, prior_epoch)
                previous = depth.new_session(policy_report, "initial")
                for update in seed_rows:
                    if update["kind"] == "update":
                        previous.remember(update)
            current = depth.new_session(policy_report, epoch["reason"], previous)
            failed_sequence = None
            for row in rows:
                try:
                    current.take(row)
                except depth.SyncLost as exc:
                    current.error, failed_sequence = str(exc), row["sequence"]
                    break
            resolved = current.error is None
            assert resolved == expected_resolved
            if not resolved:
                assert failed_sequence == rows[-1]["sequence"]
                assert current.error == epoch["final"]["error"]
            assert (
                reader.db.execute(
                    "SELECT report FROM event_collections WHERE id=?", (identity,)
                ).fetchone()[0]
                == original
            )
            cases.append(
                {
                    "collection_id": identity,
                    "epoch": epoch_id,
                    "original_reason": epoch["reason"],
                    "rows_raw_equal": len(rows),
                    "raw_artifacts_verified": sources,
                    "legacy_summary_equal": True,
                    "original_report_sha256": sha256(original.encode()),
                    "original_error": epoch["final"]["error"],
                    "scenario_resolved": resolved,
                    "scenario_failed_sequence": failed_sequence,
                    "scenario_final": current.summary(),
                    "source_segments": inputs,
                    "seed_segments": seed_inputs,
                    "scenario_scope": "Original receipts and epoch boundaries, not a prediction of changed live REST scheduling. Historical quality remains unchanged.",
                }
            )
            print(
                json_bytes(
                    {
                        "epoch": epoch_id,
                        "collection_id": identity,
                        "scenario_resolved": resolved,
                        "rows_raw_equal": len(rows),
                    }
                ).decode(),
                flush=True,
            )
        save(
            "historical-cases.json",
            {
                "checked_at": now(),
                "policy": depth.HISTORY_POLICY,
                "limits": depth.HISTORY_LIMITS,
                "cases": cases,
                "source_sha256": {
                    "depth.py": sha256(Path(depth.__file__).read_bytes()),
                    "depth_stream.py": sha256(Path(depth_stream.__file__).read_bytes()),
                    "verify_history.py": sha256(Path(__file__).read_bytes()),
                    "coverage_input_reader.py": sha256(source.read_bytes()),
                },
            },
        )
    finally:
        reader.db.close()


if __name__ == "__main__":
    main()
