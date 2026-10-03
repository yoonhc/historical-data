"""Prepare an independent recovery kit and run a source-inaccessible restore drill."""

import argparse
import hashlib
import hmac
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
KIT = Path("/mnt/backup/marketdata-recovery-kit-20261003")
WORK = Path("/mnt/backup/marketdata-independent-recovery-20261003")
RUN = ROOT / "reports/restic-independent-20261003"
PROFILE = Path("/home/yoonhc/.config/marketdata/restic-archive.toml")
SNAPSHOT = "d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad"
BACKUP = "e971e8b4b008fd5144f0ee6e8125ace882d2bdf1c6bf9f1fba23f0f5a1ac2e30"
sys.path.insert(0, str(ROOT))

from marketdata.domain import json_bytes, now  # noqa: E402

from scripts.archive_backups import Profile, load_state, repository_lock  # noqa: E402
from scripts.restic_trial import sample_streams  # noqa: E402


def save(path, value):
    with path.open("xb") as handle:
        handle.write(json_bytes(value))


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def copied(source, destination):
    assert source.is_file() and not source.is_symlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, destination.open("xb") as target:
        shutil.copyfileobj(incoming, target, 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    assert not source.samefile(destination)
    value = digest(source)
    assert digest(destination) == value
    return value


def prepare():
    profile = Profile.load(PROFILE)
    profile.guard(reserve=100 * 1024**3)
    assert not KIT.exists() and not WORK.exists() and not RUN.exists()
    began = now()
    os.umask(0o077)
    for path in (KIT, WORK, RUN):
        path.mkdir(mode=0o700)
    for relative in ("state", "cache", "runs"):
        (RUN / relative).mkdir(mode=0o700)
    (WORK / "restore-work").mkdir(mode=0o700)
    (KIT / "secrets").mkdir(mode=0o700)
    secret = KIT / "secrets/restic-password"
    with profile.restic_password_file.open("rb") as source, secret.open("xb") as target:
        shutil.copyfileobj(source, target)
        target.flush()
        os.fsync(target.fileno())
    secret.chmod(0o600)
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert not secret.samefile(profile.restic_password_file)
    assert secret.stat().st_dev != profile.restic_password_file.stat().st_dev
    assert hmac.compare_digest(secret.read_bytes(), profile.restic_password_file.read_bytes())
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=ROOT)
    (KIT / "code").mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(KIT / "code", filter="data")
    shutil.copytree(
        ROOT / ".venv/lib/python3.12/site-packages", KIT / "site-packages", symlinks=False
    )
    source_hashes = {}
    for prefix in [Path("scripts"), Path("packages/marketdata/src/marketdata")]:
        for path in sorted((KIT / "code" / prefix).rglob("*.py")):
            relative = path.relative_to(KIT / "code")
            assert digest(path) == digest(ROOT / relative)
            source_hashes[str(relative)] = digest(path)
    binary_hash = copied(profile.restic_binary, KIT / "restic")
    (KIT / "restic").chmod(0o700)
    copied(OUT / "isolation_entry.py", KIT / "isolation_entry.py")
    values = {k: str(v) if isinstance(v, Path) else v for k, v in vars(profile).items()}
    values.update(
        restic_repository="/mnt/backup/repository",
        restic_binary="/recovery/kit/restic",
        restic_password_file="/recovery/credential/restic-password",
        state_root="/recovery/state",
        reports_root="/recovery/reports",
        cache_root="/recovery/cache",
        restore_work_root="/mnt/backup/restore-work",
    )
    with (KIT / "profile.toml").open("x") as handle:
        for key, value in values.items():
            handle.write(f"{key} = {json.dumps(value)}\n")
    with repository_lock(profile):
        state = load_state(profile)
        assert state["checkpoints"][BACKUP]["stored"]["snapshot_id"] == SNAPSHOT
        assert (
            state["repository_check"]["ok"]
            and SNAPSHOT in state["repository_check"]["snapshot_ids"]
        )
        assert not list((profile.restic_repository / "locks").iterdir())
        files = sorted(p for p in profile.restic_repository.rglob("*") if p.is_file())
        snapshots_before = sorted(
            p.name for p in (profile.restic_repository / "snapshots").iterdir()
        )
        target = WORK / "repository"
        target.mkdir(mode=0o700)
        (target / "locks").mkdir(mode=0o700)
        hashes = {}
        total = 0
        for index, path in enumerate(files):
            relative = path.relative_to(profile.restic_repository)
            assert relative.parts[0] != "locks"
            hashes[str(relative)] = copied(path, target / relative)
            total += path.stat().st_size
            if index % 100 == 0:
                print(
                    json_bytes(
                        {
                            "phase": "copy-repository",
                            "files": index + 1,
                            "total_files": len(files),
                            "bytes": total,
                        }
                    ).decode(),
                    flush=True,
                )
        assert snapshots_before == sorted(
            p.name for p in (profile.restic_repository / "snapshots").iterdir()
        )
        assert files == sorted(p for p in profile.restic_repository.rglob("*") if p.is_file())
    kit = {
        "prepared_at": now(),
        "commit": commit,
        "repository_id": profile.expected_repository_id,
        "snapshot_id": SNAPSHOT,
        "backup_id": BACKUP,
        "code_sha256": source_hashes,
        "restic_binary_sha256": binary_hash,
        "python_version": sys.version,
        "credential_content_recorded": False,
        "credential_separate_inode_and_device": True,
        "credential_off_machine": False,
    }
    save(KIT / "kit.json", kit)
    save(
        OUT / "preparation.json",
        {
            **kit,
            "started_at": began,
            "kit_path": str(KIT),
            "work_path": str(WORK),
            "runtime_path": str(RUN),
            "repository_files": len(files),
            "repository_bytes": total,
            "repository_copy_sha256": hashes,
            "production_snapshot_names": snapshots_before,
            "profile_sha256": digest(PROFILE),
            "source_manifest_sha256": state["checkpoints"][BACKUP]["entry"]["manifest_sha256"],
            "operations_state_not_copied": True,
            "old_backup_not_copied": True,
        },
    )
    print(
        json_bytes(
            {
                "phase": "prepared",
                "at": now(),
                "repository_bytes": total,
                "kit": str(KIT),
                "work": str(WORK),
                "secret_content_not_printed": True,
            }
        ).decode(),
        flush=True,
    )


def run():
    assert (OUT / "preparation.json").exists()
    assert not (RUN / "launcher-result.json").exists()
    command = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--hostname",
        "marketdata-recovery",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        "/dev/disk",
        "/dev/disk",
        "--ro-bind",
        "/run/udev",
        "/run/udev",
        "--ro-bind",
        "/sys",
        "/sys",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(KIT),
        "/recovery/kit",
        "--ro-bind",
        str(KIT / "secrets"),
        "/recovery/credential",
        "--bind",
        str(WORK),
        "/mnt/backup",
        "--bind",
        str(RUN / "state"),
        "/recovery/state",
        "--bind",
        str(RUN / "cache"),
        "/recovery/cache",
        "--bind",
        str(RUN / "runs"),
        "/recovery/reports",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "TZ",
        "UTC",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "PYTHONPATH",
        "/recovery/kit/code:/recovery/kit/code/packages/marketdata/src:/recovery/kit/site-packages",
        "--chdir",
        "/recovery/kit/code",
        "--",
        "/usr/bin/python3.12",
        "-S",
        "/recovery/kit/isolation_entry.py",
    ]
    stop = threading.Event()

    def monitor():
        with (RUN / "host-stream-monitor.jsonl").open("x") as handle:
            while not stop.is_set():
                try:
                    value = sample_streams(ROOT)
                except Exception as exc:
                    value = {"at": now(), "monitor_error": str(exc)}
                handle.write(json_bytes(value).decode() + "\n")
                handle.flush()
                stop.wait(10)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    began = now()
    child = None
    try:
        save(
            RUN / "launcher-start.json",
            {"started_at": began, "command": command, "pid": os.getpid()},
        )
        with (RUN / "stdout.jsonl").open("xb") as stdout, (RUN / "stderr.txt").open("xb") as stderr:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            code = child.wait()
        save(
            RUN / "launcher-result.json",
            {"started_at": began, "finished_at": now(), "returncode": code},
        )
        print(
            json_bytes({"phase": "launcher-finished", "returncode": code, "at": now()}).decode(),
            flush=True,
        )
        if code:
            print((RUN / "stderr.txt").read_text()[-5000:], flush=True)
            raise SystemExit(code)
    except BaseException:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT)
            child.wait(timeout=120)
        raise
    finally:
        stop.set()
        watcher.join(timeout=15)


def package_evidence():
    """Keep the completed drill's instructions and receipts beside the recovery credential."""
    review = json.loads((OUT / "review.json").read_bytes())
    assert review["ok"] and (OUT / "summary.md").is_file()
    assert not (KIT / "verification").exists()
    os.umask(0o077)
    shutil.copytree(OUT, KIT / "verification", ignore=shutil.ignore_patterns("__pycache__"))
    copied(OUT / "RECOVERY.md", KIT / "RECOVERY.md")
    copied(ROOT / "docs/restic-transition-and-retention.md", KIT / "retention-policy.md")
    receipt = {
        "copied_at": now(),
        "kit_path": str(KIT),
        "snapshot_id": SNAPSHOT,
        "review_sha256": digest(KIT / "verification/review.json"),
        "instructions_sha256": digest(KIT / "RECOVERY.md"),
        "credential_content_or_hash_recorded": False,
    }
    save(OUT / "kit-evidence-copy.json", receipt)
    print(json_bytes(receipt).decode(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "package-evidence"))
    args = parser.parse_args()
    os.nice(10)
    subprocess.run(["ionice", "-c", "2", "-n", "7", "-p", str(os.getpid())], check=True)
    if args.action == "prepare":
        prepare()
    elif args.action == "run":
        run()
    else:
        package_evidence()
