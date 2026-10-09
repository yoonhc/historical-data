"""Seal evidence and check unchanged inputs; never mutate the operating store."""

import hashlib
import json
import re
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    inputs = json.loads((OUT / "inputs.json").read_bytes())
    protected = {p: sha(ROOT / p) == h for p, h in inputs["protected_files"].items()}
    assert all(protected.values())
    uri = (ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        unchanged = {}
        for case in inputs["cases"]:
            original = db.execute(
                "SELECT report FROM event_collections WHERE id=?", (case["collection_id"],)
            ).fetchone()[0]
            unchanged[case["collection_id"]] = (
                hashlib.sha256(original.encode()).hexdigest() == case["original_report_sha256"]
            )
        assert all(unchanged.values())
    db.close()
    tests = ET.parse(OUT / "tests.xml").getroot().find("testsuite").attrib
    assert tests["tests"] == "6" and all(tests[k] == "0" for k in ("errors", "failures", "skipped"))
    checked_links = []
    for path in (OUT / "summary.md", ROOT / "docs/orderbook-depth.md", ROOT / "reports/README.md"):
        for link in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", path.read_text()):
            if "://" in link or link.startswith("#"):
                continue
            destination = (path.parent / link.split("#")[0]).resolve()
            assert destination.exists() or destination == OUT / "validation.json", (path, link)
            checked_links.append({"source": str(path.relative_to(ROOT)), "target": link})
    checks = []
    for command in (
        [".venv/bin/ruff", "check", str(OUT), "tests/test_depth_independent_cases.py"],
        [".venv/bin/ruff", "format", "--check", str(OUT), "tests/test_depth_independent_cases.py"],
        ["git", "diff", "--check"],
    ):
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
        checks.append({"command": command, "exit_code": run.returncode, "output": run.stdout})
    streams = {}
    at = datetime.now(UTC)
    for name in ("depth-latest.json", "aggtrade-latest.json"):
        p = ROOT / "state" / name
        if not p.exists():
            continue
        report = json.loads(p.read_bytes())
        received = report.get("last_received_at")
        streams[name] = {
            "id": report.get("id"),
            "state": report.get("state"),
            "last_received_at": received,
            "receive_age_seconds": (at - datetime.fromisoformat(received)).total_seconds()
            if received
            else None,
            "messages": report.get("messages"),
            "sync_losses": report.get("sync_losses"),
        }
    files = {
        str(p.relative_to(ROOT)): sha(p)
        for p in [
            *sorted(p for p in OUT.iterdir() if p.is_file()),
            ROOT / "tests/test_depth_independent_cases.py",
            ROOT / "docs/orderbook-depth.md",
            ROOT / "reports/README.md",
        ]
    }
    result = {
        "checked_at": at.isoformat(),
        "protected_inputs_unchanged": protected,
        "original_catalog_reports_unchanged": unchanged,
        "tests": tests,
        "checks": checks,
        "local_links": checked_links,
        "files_sha256": files,
        "runtime_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "current_stream_samples": streams,
        "limits": [
            "Six new tests characterize the existing status-publication gap; they do not fix it.",
            "Stream samples are observations at one instant, not uninterrupted uptime evidence.",
            "No operating code, request budget, service, raw data, existing report or quality verdict changed.",
            "Post-retirement daily/restic follow-up is a separate outstanding task.",
        ],
    }
    with (OUT / "validation.json").open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {
                "protected_files": len(protected),
                "all_unchanged": all(protected.values()),
                "tests": tests,
                "files": len(files),
                "links": len(checked_links),
                "streams": streams,
            }
        )
    )


if __name__ == "__main__":
    main()
