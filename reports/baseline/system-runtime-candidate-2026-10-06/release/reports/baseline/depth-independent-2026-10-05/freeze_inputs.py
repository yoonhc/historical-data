"""Copy only the two failure epochs into an offline raw-input fixture.

Production Store is used solely for verified, layout-locked file reads. No Book,
Session, History, replay, collector, or exchange request is used here.
"""

import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from marketdata.store import Store

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = OUT.parent / "depth-decisions-observation-2026-10-05"
CASES = {
    "c595e79851ef4ced8e4194f22cf58b84": (2, 17825, 25274),
    "54d28ea721ed41fab80928c064514159": (3, 26747, 27517),
}


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n")


class Reader(Store):
    def __init__(self):
        self.root = ROOT
        uri = (ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro"
        self.db = sqlite3.connect(uri, uri=True, isolation_level=None)
        self._files_db = sqlite3.connect(uri, uri=True, isolation_level=None)
        self.db.row_factory = self._files_db.row_factory = sqlite3.Row


def source_row(row, payload):
    if row["kind"] == "update":
        envelope = payload[row["source_row"]]
        assert envelope["sequence"] == row["sequence"] and envelope["epoch"] == row["epoch"]
        assert datetime.fromisoformat(envelope["received_at"]) == row["received_at"]
        raw = json.loads(envelope["message"])
        assert (raw["U"], raw["u"], raw["pu"]) == (
            row["first_update_id"],
            row["final_update_id"],
            row["previous_update_id"],
        )
        fields = ("b", "a")
    else:
        raw, fields = payload, ("bids", "asks")
        assert raw["lastUpdateId"] == row["final_update_id"]
    assert datetime.fromtimestamp(raw["T"] / 1000, UTC) == row["timestamp"]
    assert datetime.fromtimestamp(raw["E"] / 1000, UTC) == row["event_time"]
    for side, key in zip(("bids", "asks"), fields, strict=True):
        assert [(r["price"], r["quantity"]) for r in row[side]] == [
            (Decimal(p), Decimal(q)) for p, q in raw[key]
        ]
    return raw


def main():
    started = datetime.now(UTC).isoformat()
    reports = {r["id"]: r for r in json.loads((PRIOR / "book_depth-reports.json").read_bytes())}
    protected = {
        str(p.relative_to(ROOT)): digest(p.read_bytes())
        for p in [
            *sorted((ROOT / "packages").rglob("*.py")),
            *sorted(p for p in PRIOR.iterdir() if p.is_file()),
        ]
    }
    reader, manifests = Reader(), []
    try:
        for cid, (epoch, start, end) in CASES.items():
            original = reader.db.execute(
                "SELECT report FROM event_collections WHERE id=?", (cid,)
            ).fetchone()[0]
            assert json.loads(original) == reports[cid]
            report = reports[cid]
            assert report["epochs"][epoch]["reason"] == "planned_rotation"
            save(f"{cid}-report.json", report)
            records = reader.db.execute(
                "SELECT * FROM event_segments WHERE collection_id=? "
                "AND last_id>=? AND first_id<=? ORDER BY first_id",
                (cid, start, end),
            ).fetchall()
            sources, segments, lines, pairs, expected = {}, [], [], 0, start
            for record in records:
                payloads = {}
                segments.append({k: record[k] for k in ("id", "path", "file_sha", "row_count")})
                for row in reader._revision_table(record).to_pylist():
                    if not start <= row["sequence"] <= end:
                        continue
                    assert row["sequence"] == expected and row["epoch"] == epoch
                    expected += 1
                    sid = row["source_id"]
                    if sid not in payloads:
                        blob, _ = reader.raw(sid)
                        assert digest(blob) == sid
                        payloads[sid] = json.loads(
                            gzip.decompress(blob) if row["kind"] == "update" else blob
                        )
                        sources[sid] = {"sha256": sid, "bytes": len(blob)}
                    raw = source_row(row, payloads[sid])
                    pairs += len(row["bids"]) + len(row["asks"])
                    lines.append(
                        encoded(
                            {
                                **{
                                    k: row[k]
                                    for k in (
                                        "sequence",
                                        "epoch",
                                        "kind",
                                        "source_id",
                                        "source_row",
                                    )
                                },
                                "received_at": row["received_at"].isoformat(),
                                "raw": raw,
                            }
                        )
                    )
            assert expected == end + 1
            bundle = gzip.compress(b"\n".join(lines) + b"\n", mtime=0)
            name = f"{cid}-raw.jsonl.gz"
            with (OUT / name).open("xb") as handle:
                handle.write(bundle)
            manifests.append(
                {
                    "collection_id": cid,
                    "epoch": epoch,
                    "first_sequence": start,
                    "last_sequence": end,
                    "rows": len(lines),
                    "price_quantity_pairs": pairs,
                    "raw_canonical_equal": True,
                    "bundle": name,
                    "bundle_sha256": digest(bundle),
                    "bundle_bytes": len(bundle),
                    "raw_sources": sources,
                    "segments": segments,
                    "original_report_sha256": digest(original.encode()),
                    "report_file_sha256": digest((OUT / f"{cid}-report.json").read_bytes()),
                }
            )
            print(
                json.dumps(
                    {
                        k: manifests[-1][k]
                        for k in ("collection_id", "rows", "price_quantity_pairs", "bundle_bytes")
                    }
                ),
                flush=True,
            )
    finally:
        reader.close()
    assert all(digest((ROOT / p).read_bytes()) == h for p, h in protected.items())
    save(
        "inputs.json",
        {
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "scope": "Only two failure epochs; offline calculation starts after closing live catalog",
            "catalog_mode": "mode=ro with live WAL; per-file shared layout locks only",
            "cases": manifests,
            "protected_files": protected,
            "input_note": "Parsed original payloads reserialized without changing price/quantity strings; "
            "source hashes identify untouched original artifacts. Canonical parity checked during copy.",
        },
    )


if __name__ == "__main__":
    main()
