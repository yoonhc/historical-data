"""Read-only independent audit of the supplied OI research archive (stdlib only)."""

import argparse
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rows(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) != 1 or members[0].file_size > 16_000_000:
            raise ValueError("Unexpected archive members or size")
        return list(csv.DictReader(io.StringIO(zf.read(members[0]).decode())))


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def audit(root):
    seen, files, versions = set(), [], {}
    for path in sorted((root / "data/raw").rglob("BTCUSDT-metrics-*.zip")):
        blob = path.read_bytes()
        sha = digest(blob)
        checksum = Path(str(path) + ".CHECKSUM")
        if not checksum.is_file() or checksum.read_text().split()[0].lower() != sha:
            raise ValueError(f"Checksum mismatch or absent: {path}")
        versions.setdefault(path.name, set()).add(sha)
        if (path.name, sha) in seen:
            continue
        seen.add((path.name, sha))
        day = path.name.removeprefix("BTCUSDT-metrics-").removesuffix(".zip")
        incoming = rows(blob)
        labels = [stamp(r["create_time"]) for r in incoming]
        start = stamp(day)
        expected = {start + timedelta(minutes=5 * i) for i in range(288)}
        zero, invalid = [], []
        for i, row in enumerate(incoming):
            values = [Decimal(row[k]) for k in ("sum_open_interest", "sum_open_interest_value")]
            if row["symbol"] != "BTCUSDT" or any(not v.is_finite() or v < 0 for v in values):
                invalid.append(i)
            elif any(v == 0 for v in values):
                zero.append(i)
        files.append(
            {
                "date": day,
                "path": str(path.relative_to(root)),
                "sha256": sha,
                "rows": len(incoming),
                "missing": len(expected - set(labels)),
                "duplicate_keys": len(labels) - len(set(labels)),
                "off_grid_or_outside": len(set(labels) - expected),
                "reversed_adjacent": sum(b < a for a, b in zip(labels, labels[1:])),
                "zero_rows": zero,
                "invalid_rows": invalid,
            }
        )
    summary_doc = root / "research/price_shock_oi_5m/offset_followup_2026-09-14.json"
    summary = json.loads(summary_doc.read_text())
    run = root / summary["run"]
    by_hash = {}
    for path in run.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            by_hash[digest(blob)] = blob
    archive_rows = {}
    for item in summary["archives"]:
        for record in item["files"]:
            blob = by_hash[record["sha256"]]
            if record["url"].endswith(".zip"):
                archive_rows.update(
                    {
                        int(stamp(r["create_time"]).timestamp() * 1000): tuple(
                            Decimal(r[k]) for k in ("sum_open_interest", "sum_open_interest_value")
                        )
                        for r in rows(blob)
                    }
                )
    comparisons = []
    for record in summary["rest_requests"]:
        for row in json.loads(by_hash[record["sha256"]]):
            values = tuple(Decimal(row[k]) for k in ("sumOpenInterest", "sumOpenInterestValue"))
            ts = row["timestamp"]
            comparisons.append(
                {
                    "rest_timestamp": ts,
                    "minus_5m_exact": archive_rows.get(ts - 300_000) == values,
                    "same_label_exact": archive_rows.get(ts) == values,
                }
            )
    initial = [f for f in files if "2024-01-01" <= f["date"] < "2024-05-01"]
    # One vintage per date in this subset is required to compare totals.
    assert len({f["date"] for f in initial}) == len(initial)
    initial_counts = {
        "files": len(initial),
        "rows": sum(f["rows"] for f in initial),
        "missing": sum(f["missing"] for f in initial),
        "unordered_files": sum(f["reversed_adjacent"] > 0 for f in initial),
        "reversed_adjacent": sum(f["reversed_adjacent"] for f in initial),
    }
    return {
        "archive_root": str(root),
        "audit_script_sha256": digest(Path(__file__).read_bytes()),
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "unique_files": len(files),
        "date_min": min(f["date"] for f in files),
        "date_max": max(f["date"] for f in files),
        "checksum_verification": "all audited ZIPs matched adjacent CHECKSUM",
        "conflicting_vintages": {k: sorted(v) for k, v in versions.items() if len(v) > 1},
        "totals": {
            k: sum(f[k] for f in files)
            for k in (
                "rows",
                "missing",
                "duplicate_keys",
                "off_grid_or_outside",
                "reversed_adjacent",
            )
        },
        "unordered_files": sum(f["reversed_adjacent"] > 0 for f in files),
        "zero_days": {f["date"]: len(f["zero_rows"]) for f in files if f["zero_rows"]},
        "invalid_days": [f["date"] for f in files if f["invalid_rows"]],
        "initial_2024_replay": initial_counts,
        "initial_2024_matches_prior": initial_counts
        == {
            "files": 121,
            "rows": 34723,
            "missing": 125,
            "unordered_files": 27,
            "reversed_adjacent": 1424,
        },
        "offset_replay": {
            "samples": len(comparisons),
            "minus_5m_exact": sum(c["minus_5m_exact"] for c in comparisons),
            "same_label_exact": sum(c["same_label_exact"] for c in comparisons),
            "details": comparisons,
        },
        "interpretation": "Historical replay only. No universal clock shift or past availability inferred.",
        "files": files,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.archive.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"files", "offset_replay"}},
            indent=2,
            ensure_ascii=False,
        )
    )
    print(json.dumps({k: v for k, v in result["offset_replay"].items() if k != "details"}))
