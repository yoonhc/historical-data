"""Bounded public-source observation using a separate clock-disabled Chrony 4.5."""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOSTS = {
    "cloudflare": "time.cloudflare.com",
    "nict": "ntp.nict.jp",
    "netnod": "ntp.netnod.se",
    "ptb": "ptbtime1.ptb.de",
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=int, default=1800)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-set", type=Path)
    a = p.parse_args()
    if not 60 <= a.seconds <= 3600 or os.geteuid() == 0:
        raise SystemExit("Bounded non-root observation required")
    a.output.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix="md-source-observe-"))
    spec = importlib.util.spec_from_file_location("rt", ROOT / "work/marketdata_runtime.py")
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    policy = json.loads(Path("/etc/marketdata/runtime.json").read_text())["policy"]
    selection = (
        json.loads(a.source_set.read_text())
        if a.source_set
        else [{"operator": k, "hostname": v} for k, v in HOSTS.items()]
    )
    if len(selection) != 4:
        raise RuntimeError("A bounded set of four candidates is required")
    sources = []
    for item in selection:
        operator, host = item["operator"], item["hostname"]
        addresses = sorted(
            {r[4][0] for r in socket.getaddrinfo(host, 123, socket.AF_INET, socket.SOCK_DGRAM)}
        )
        selected = item.get("address", addresses[0] if addresses else None)
        if not addresses or selected not in addresses or selected == "91.189.91.157":
            raise RuntimeError("Missing/unreviewed DNS result: " + host)
        sources.append(
            {
                "operator": operator,
                "hostname": host,
                "resolved_at": now(),
                "ipv4_answers": addresses,
                "observed_address": selected,
            }
        )
    sock = work / "chronyd.sock"
    config = "".join(
        f"server {s['observed_address']} iburst minpoll 6 maxpoll 8\n" for s in sources
    )
    config += (
        f"pidfile {work}/chronyd.pid\ndriftfile {work}/drift\nlogdir {a.output.resolve()}\n"
        "log measurements statistics tracking selection\nport 0\ncmdport 0\n"
        f"bindcmdaddress {sock}\nmaxupdateskew 100.0\nleapsectz right/UTC\nleapsecmode slew\n"
    )
    (work / "chrony.conf").write_text(config)
    (a.output / "chrony.conf").write_text(config)
    # A private, hash-identical test executable avoids using the package service's
    # path-based AppArmor profile for a completely separate unprivileged daemon.
    # No installed executable, profile, capability, or clock is changed.
    binary = work / "chronyd"
    shutil.copyfile("/usr/sbin/chronyd", binary)
    binary.chmod(0o700)
    assert binary.read_bytes() == Path("/usr/sbin/chronyd").read_bytes()
    argv = [
        str(binary),
        "-x",
        "-U",
        "-u",
        pwd.getpwuid(os.getuid()).pw_name,
        "-n",
        "-f",
        str(work / "chrony.conf"),
    ]
    result = {
        "scope": "public_candidates_separate_chronyd_clock_control_disabled",
        "sources": sources,
        "started_at": now(),
        "requested_seconds": a.seconds,
        "work": str(work),
        "argv": argv,
        "host_accuracy_certified": False,
        "binary_sha256": hashlib.sha256(Path(argv[0]).read_bytes()).hexdigest(),
        "version": subprocess.check_output([argv[0], "-v"], text=True).strip(),
    }
    (a.output / "start.json").write_text(json.dumps(result, indent=2) + "\n")
    child = None
    try:
        with (
            (a.output / "daemon.log").open("w") as log,
            (a.output / "observations.jsonl").open("w") as out,
        ):
            child = subprocess.Popen(argv, stdout=log, stderr=log)
            began = time.monotonic()
            while not sock.exists() and time.monotonic() - began < 5:
                if child.poll() is not None:
                    raise RuntimeError((a.output / "daemon.log").read_text())
                time.sleep(0.1)
            status = Path(f"/proc/{child.pid}/status").read_text()
            caps = next(line for line in status.splitlines() if line.startswith("CapEff:"))
            if int(caps.split()[1], 16) != 0:
                raise RuntimeError("Test process unexpectedly has capabilities")
            result["capabilities"] = caps
            next_sample = began
            index = 0
            while True:
                if child.poll() is not None:
                    raise RuntimeError("Observation daemon exited")
                record = {
                    "index": index,
                    "at": now(),
                    "monotonic": time.monotonic(),
                    "commands": {},
                }
                for command in ("tracking", "sources", "sourcestats", "selectdata", "ntpdata"):
                    q = subprocess.run(
                        ["/usr/bin/chronyc", "-n", "-c", "-h", str(sock), command],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    record["commands"][command] = {
                        "returncode": q.returncode,
                        "stdout": q.stdout,
                        "stderr": q.stderr,
                    }
                q = record["commands"]["tracking"]
                if q["returncode"] == 0:
                    t = rt.tracking(q["stdout"])
                    record["clock_assessment"] = rt.assess_clock(t, policy, time.time())
                out.write(json.dumps(record, sort_keys=True) + "\n")
                out.flush()
                index += 1
                elapsed = time.monotonic() - began
                if index % 2 == 1:
                    print(
                        json.dumps(
                            {
                                "elapsed": round(elapsed),
                                "sample": index,
                                "ready": record.get("clock_assessment", {}).get("ready"),
                            }
                        ),
                        flush=True,
                    )
                if elapsed >= a.seconds:
                    break
                next_sample += 30
                time.sleep(max(0, min(next_sample - time.monotonic(), a.seconds - elapsed)))
            result.update(
                completed=True, observed_seconds=time.monotonic() - began, sample_count=index
            )
    except BaseException as exc:
        result.update(completed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=10)
        result.update(
            finished_at=now(),
            child_exit=None if child is None else child.returncode,
            socket_removed=not sock.exists(),
        )
        (a.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
