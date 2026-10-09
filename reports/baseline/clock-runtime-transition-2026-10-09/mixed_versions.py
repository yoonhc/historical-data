"""Real version/process/storage integration; exchange and mount identity are fixtures.

Never receives an operating store/profile/password. All mutable paths must be
under a newly created /tmp/clock-mixed-* directory. No forget/prune commands.
"""

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NEW = Path("/tmp/clock-shutdown-correction-cpmkuynr")
OLD = Path("/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee")
REPORT = Path(__file__).parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n")


def capture(root, newer):
    import marketdata

    # Append test-only dependencies after the selected runtime installation.
    sys.path.append(str(NEW / ".venv/lib/python3.12/site-packages"))
    import pytest
    from marketdata.store import Store

    # Test helpers inject transport/clock only. Runtime modules still come from
    # this process's selected installation, not from the test source directory.
    sys.path.insert(0, str(NEW / "tests"))
    d = importlib.import_module("test_depth")
    a = importlib.import_module("test_aggtrades")
    ce = importlib.import_module("marketdata.clock_evidence") if newer else None
    with closing(Store(root / "store")) as store:
        result = {"module": marketdata.__file__, "collections": []}
        for stream in ("depth", "aggtrade"):
            with pytest.MonkeyPatch.context() as mp:
                if newer:
                    import uuid

                    b = {
                        "schema": 2,
                        "run_id": uuid.uuid4().hex,
                        "child_id": uuid.uuid4().hex,
                        "boot_id": "11111111-1111-1111-1111-111111111111",
                        "stream": stream,
                        "policy_version": ce.POLICY,
                    }

                    def stamp(t):
                        return {
                            "wall_ns": 1704067200000000000 + int(t * 1e9),
                            "monotonic_ns": int(t * 1e9),
                            "boottime_ns": int(t * 1e9),
                            "sampling_span_ns": 500,
                        }

                    mp.setenv("MARKETDATA_CLOCK_CONTEXT", json.dumps(b))
                    samples = iter(range(1, 100))
                    mp.setattr(ce, "sample", lambda: stamp(next(samples)))
                    journal = ce.Journal(store, root / ("spool-" + stream), b, min_free_bytes=0)
                else:
                    mp.delenv("MARKETDATA_CLOCK_CONTEXT", raising=False)
                if stream == "depth":
                    first = 100 if newer else 200
                    report = d.capture(
                        store, mp, [[d.update(first), d.update(first + 1)]], [d.snapshot(first)]
                    )
                else:
                    first = 1 if newer else 3
                    connector = a.connector_factory(
                        mp, [[a.trade(first, ws=True), a.trade(first + 1, ws=True)]]
                    )
                    report = a.live.capture(store, connector=connector, seconds=11, min_free_gib=0)
                assert report["state"] in ("partial", "complete"), report
                if newer:
                    decision = {
                        "reference_stale": True,
                        "current_estimate_usable": True,
                        "raw_start_allowed": True,
                        "reasons": [],
                    }
                    for t in range(0, 30):
                        journal.write(
                            "finished" if t == 29 else "running",
                            sample_value=stamp(t),
                            decision=decision,
                            history={"reference_stale_seen": True, "hard_fault_seen": False},
                        )
                    journal.flush()
                    q = ce.query(store, report["id"])
                    assert q["evidence_state"] == "available", q
                    save(root / ("query-" + stream + ".json"), q)
                result["collections"].append(
                    {"id": report["id"], "state": report["state"], "stream": stream}
                )
        assert store.validate()["ok"]
        result["artifacts"] = [dict(r) for r in store.db.execute("SELECT * FROM artifacts")]
        result["indexes"] = [
            r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        ]
        assert "fetches_url" in result["indexes"]
    return result


def archive(root, number):
    import marketdata
    import pytest
    from marketdata import backup, maintenance, packing
    from marketdata.store import Store

    sys.path.insert(0, str(ROOT))
    from scripts import archive_backups as ar

    disk = root / "disk"
    repo, password = disk / "repository", root / "password"
    binary = ROOT / ".cache/restic/0.19.1/restic"
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESTIC_")}
    cmd = [
        str(binary),
        "--repo",
        str(repo),
        "--password-file",
        str(password),
        "--no-cache",
        "--json",
    ]
    if number == 1:
        repo.mkdir(parents=True)
        password.write_text(os.urandom(32).hex() + "\n")
        password.chmod(0o600)
        subprocess.run(cmd + ["init"], check=True, capture_output=True, env=env)
    identity = json.loads(subprocess.check_output(cmd + ["cat", "config"], env=env))["id"]
    p = ar.Profile(
        root / "store",
        disk / "backups",
        disk,
        "isolated-test-uuid",
        repo,
        identity,
        binary,
        password,
        "clock-mixed-fixture-v1",
        "2026-10-07",
        root / "archive-state",
        root / "archive-reports",
        root / "cache",
        disk / "recovery",
        1,
        3,
        2,
    )
    p.backup_dir.mkdir(exist_ok=True)
    with pytest.MonkeyPatch.context() as mp, closing(Store(root / "store")) as store:
        # Only dedicated filesystem identity is simulated. Real restic binary,
        # catalog/manifest guards, hashes, locks, full pack check and restore run.
        mp.setattr(ar, "mount_identity", lambda _: "isolated-test-uuid")
        pack = packing.pack_events(store, apply=True, before="2100-01-01", min_free_gib=0)
        assert pack["files"] > 0
        instant = datetime(2026, 10, 6 + number, 4, 10, tzinfo=timezone.utc)
        mp.setattr(backup, "now", lambda: instant)
        checkpoint = maintenance.daily_backup(
            store, instant.date().isoformat(), 0, None, directory=p.backup_dir, incremental=True
        )
        assert checkpoint["ok"]
        stored = ar.execute(p, "run")
        assert stored["ok"], stored
        record = ar.load_state(p)["checkpoints"][checkpoint["backup_id"]]
        sid = record["stored"]["snapshot_id"]
        assert sid in stored["result"]["stored_snapshots"]
        repeat = ar.execute(p, "run")
        assert repeat["ok"] and repeat["result"]["processed"] == 0
        proof = ar.execute(p, "restore", snapshot_id=sid)
        assert proof["ok"] and proof["result"]["ok"]
        extracted = Path(proof["result"]["extracted"])
        assert (
            json.loads((extracted / "backup.json").read_text())["backup_id"]
            == checkpoint["backup_id"]
        )
        assert store.validate()["ok"]
    return {
        "module": marketdata.__file__,
        "pack": pack,
        "checkpoint": checkpoint,
        "stored": stored,
        "repeat": repeat,
        "restore": proof,
    }


def query(root, phase):
    from marketdata import clock_evidence as ce

    saved = json.loads((root / "new-capture.json").read_text())
    ar = json.loads((root / f"archive-{phase}.json").read_text())
    proof = ar["restore"]["result"]
    targets = [
        root / "store",
        Path(ar["checkpoint"]["path"]),
        Path(proof["extracted"]),
        Path(proof["restored"]),
        Path(proof["new_backup"]),
    ]
    results = []
    for target in targets:
        for c in saved["collections"]:
            original = json.loads((root / ("query-" + c["stream"] + ".json")).read_text())
            cli = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "marketdata.cli",
                    "--root",
                    str(target),
                    "clock-evidence",
                    "--collection-id",
                    c["id"],
                ],
                capture_output=True,
                text=True,
            )
            assert cli.returncode == 0, cli.stderr
            assert json.loads(cli.stdout) == original == ce.query_root(target, c["id"])
            results.append({"root": str(target), "collection_id": c["id"], "equal": True})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--action",
        choices=["new-capture", "old-capture", "archive-1", "archive-2", "query-1", "query-2"],
    )
    ap.add_argument("--fixture", type=Path)
    args = ap.parse_args()
    if args.action:
        root = args.fixture.resolve()
        assert root.parent == Path("/tmp") and root.name.startswith("clock-mixed-")
        if args.action.endswith("capture"):
            value = capture(root, args.action == "new-capture")
        elif args.action.startswith("archive"):
            value = archive(root, int(args.action[-1]))
        else:
            value = query(root, int(args.action[-1]))
        save(root / (args.action + ".json"), value)
        return
    root = Path(tempfile.mkdtemp(prefix="clock-mixed-"))
    sequence = [
        ("new-capture", NEW),
        ("archive-1", ROOT),
        ("query-1", NEW),
        ("old-capture", OLD),
        ("archive-2", ROOT),
        ("query-2", NEW),
    ]
    out = {
        "fixture": str(root),
        "passed": False,
        "steps": [],
        "simulated": ["exchange transport/clock", "dedicated backup mount identity"],
        "versions": {"new": str(NEW), "old": str(OLD), "maintenance": str(ROOT)},
    }
    try:
        for action, version in sequence:
            p = subprocess.run(
                [
                    str(version / ".venv/bin/python"),
                    "-I",
                    str(Path(__file__).resolve()),
                    "--action",
                    action,
                    "--fixture",
                    str(root),
                ],
                capture_output=True,
                text=True,
            )
            (root / (action + ".log")).write_text(p.stdout + p.stderr)
            out["steps"].append(
                {
                    "action": action,
                    "python": str(version / ".venv/bin/python"),
                    "returncode": p.returncode,
                }
            )
            assert p.returncode == 0, (action, p.stderr)
        new = json.loads((root / "new-capture.json").read_text())
        from marketdata.store import Store

        with closing(Store(root / "store")) as s:
            for a in new["artifacts"]:
                assert digest(s.root / a["path"]) == a["id"]
        out["passed"] = True
        out["new_artifacts_unchanged"] = len(new["artifacts"])
    finally:
        for path in root.glob("*.json"):
            if path.name != "password":
                out[path.stem] = json.loads(path.read_text())
        save(REPORT / "mixed-versions-result.json", out)
        print(
            json.dumps(
                {"passed": out["passed"], "fixture": str(root), "steps": out["steps"]}, indent=2
            )
        )


if __name__ == "__main__":
    main()
