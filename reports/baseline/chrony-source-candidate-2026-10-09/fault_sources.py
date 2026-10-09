"""Real Chrony 4.5, synthetic NTP loss in a private rootless network namespace.

Real elapsed seconds and unchanged 600-second clock assessment. No reselect,
prefer, makestep, host network rules, or host clock control.
"""

import argparse
import concurrent.futures
import datetime
import importlib.util
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def ntp_stamp():
    t = time.time() + 2208988800
    return struct.pack("!II", int(t), int((t % 1) * 2**32))


class Sources:
    def __init__(self, case, addresses, output):
        self.case, self.addresses, self.output = case, addresses, output
        self.stop = threading.Event()
        self.fault_at = None
        self.threads, self.sockets = [], []
        self.counts = [{"rx": 0, "tx": 0, "dropped": 0, "fault_requests": 0} for _ in addresses]
        self.lock = threading.Lock()

    def start(self):
        for i, ip in enumerate(self.addresses):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((ip, 1123))
            sock.settimeout(0.2)
            self.sockets.append(sock)
            t = threading.Thread(target=self.answer, args=(i, sock))
            t.start()
            self.threads.append(t)

    def answer(self, index, sock):
        with (self.output / f"source-{index}.jsonl").open("w") as out:
            while not self.stop.is_set():
                try:
                    req, peer = sock.recvfrom(1024)
                except socket.timeout:
                    continue
                received = ntp_stamp()
                self.counts[index]["rx"] += 1
                dropped = False
                if index == 0 and self.fault_at is not None:
                    self.counts[index]["fault_requests"] += 1
                    n = self.counts[index]["fault_requests"]
                    # Intermittent: four consecutive missing replies, then two
                    # responses. No manufactured past real-world measurements.
                    dropped = self.case == "silent" or (n - 1) % 6 < 4
                if dropped:
                    self.counts[index]["dropped"] += 1
                else:
                    response = bytearray(48)
                    response[:4] = bytes([0x24, 1, req[2], 236])
                    # Lower declared root distance naturally selects source 0.
                    # No selection hint or selection command is used.
                    response[4:8] = struct.pack(
                        "!I", int((0.002 if index == 0 else 0.03 + index * 0.01) * 65536)
                    )
                    response[8:12] = struct.pack("!I", 66)
                    response[12:16] = b"TEST"
                    response[16:24] = received
                    response[24:32] = req[40:48]
                    response[32:40] = received
                    response[40:48] = ntp_stamp()
                    sock.sendto(response, peer)
                    self.counts[index]["tx"] += 1
                out.write(
                    json.dumps(
                        {
                            "at": utc(),
                            "monotonic": time.monotonic(),
                            "drop": dropped,
                            "client": peer,
                            "counters": dict(self.counts[index]),
                        }
                    )
                    + "\n"
                )
                out.flush()

    def finish(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(2)
        for sock in self.sockets:
            sock.close()


def run_case(case, addresses, root, warmup, duration, rt, policy):
    out = root / case
    out.mkdir()
    work = Path(tempfile.mkdtemp(prefix=f"md-source-{case}-"))
    binary = work / "chronyd"
    shutil.copyfile("/usr/sbin/chronyd", binary)
    binary.chmod(0o700)
    sock = work / "chronyd.sock"
    conf = "".join(f"server {ip} port 1123 iburst minpoll 6 maxpoll 8\n" for ip in addresses)
    conf += (
        f"pidfile {work}/pid\ndriftfile {work}/drift\nlogdir {out}\n"
        "log measurements statistics tracking selection\nport 0\ncmdport 0\n"
        f"bindcmdaddress {sock}\nmaxupdateskew 100.0\nleapsectz right/UTC\nleapsecmode slew\n"
    )
    (work / "chrony.conf").write_text(conf)
    (out / "chrony.conf").write_text(conf)
    argv = [
        "/usr/bin/setpriv",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        str(binary),
        "-x",
        "-U",
        "-u",
        "root",
        "-n",
        "-f",
        str(work / "chrony.conf"),
    ]
    servers = Sources(case, addresses, out)
    servers.start()
    result = {
        "case": case,
        "started_at": utc(),
        "argv": argv,
        "sources": addresses,
        "warmup_seconds": warmup,
        "fault_window_seconds": duration,
        "policy": policy,
        "host_clock_control": False,
        "full_operating_provider_gate": "not asserted: test daemon is intentionally -x",
        "assessment": "unchanged assess_clock on actual Chrony CSV; synthetic TEST-NET source clocks",
    }
    child = None
    try:
        with (
            (out / "daemon.log").open("w") as log,
            (out / "observations.jsonl").open("w") as stream,
        ):
            child = subprocess.Popen(argv, stdout=log, stderr=log)
            start = time.monotonic()
            while not sock.exists() and time.monotonic() - start < 5:
                if child.poll() is not None:
                    raise RuntimeError((out / "daemon.log").read_text())
                time.sleep(0.1)
            caps = next(
                s
                for s in Path(f"/proc/{child.pid}/status").read_text().splitlines()
                if s.startswith("CapEff:")
            )
            assert int(caps.split()[1], 16) == 0
            result["capabilities"] = caps
            fault_started = False
            last_reference = None
            events = []
            next_sample = start
            while time.monotonic() - start < warmup + duration:
                if child.poll() is not None:
                    raise RuntimeError("Isolated chronyd exited")
                row = {
                    "at": utc(),
                    "elapsed": time.monotonic() - start,
                    "monotonic": time.monotonic(),
                    "queries": {},
                }
                for command in ("tracking", "sources", "selectdata", "ntpdata"):
                    q = subprocess.run(
                        ["/usr/bin/chronyc", "-n", "-c", "-h", str(sock), command],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    row["queries"][command] = {
                        "rc": q.returncode,
                        "stdout": q.stdout,
                        "stderr": q.stderr,
                    }
                q = row["queries"]["tracking"]
                if q["rc"] == 0:
                    t = rt.tracking(q["stdout"])
                    row["assessment"] = rt.assess_clock(t, policy, time.time())
                    key = (t["reference_address"], t["reference_time"])
                    if key != last_reference:
                        event = {
                            "kind": "reference_changed",
                            "at": row["at"],
                            "elapsed": row["elapsed"],
                            "reference_address": key[0],
                            "reference_time": key[1],
                            "assessment": row["assessment"],
                        }
                        events.append(event)
                        last_reference = key
                    if not fault_started and row["elapsed"] >= warmup:
                        if t["reference_address"] != addresses[0] or not row["assessment"]["ready"]:
                            raise RuntimeError(
                                "Primary source not naturally selected/ready at fault boundary"
                            )
                        result["last_valid_reference_before_fault"] = {
                            "address": key[0],
                            "epoch": key[1],
                        }
                        result["fault_started_at"] = row["at"]
                        result["fault_started_elapsed"] = row["elapsed"]
                        servers.fault_at = time.monotonic()
                        fault_started = True
                        print(json.dumps({"case": case, "fault_started": row["at"]}), flush=True)
                row["fault_active"] = fault_started
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
                next_sample += 5
                time.sleep(max(0, next_sample - time.monotonic()))
            result.update(completed=True, reference_events=events, counters=servers.counts)
    except BaseException as exc:
        result.update(completed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=10)
        servers.finish()
        result.update(finished_at=utc(), child_exit=None if child is None else child.returncode)
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return {"case": case, "completed": result["completed"], "counters": result["counters"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--warmup", type=int, default=720)
    p.add_argument("--seconds", type=int, default=1800)
    p.add_argument("--parent-netns")
    a = p.parse_args()
    if a.parent_netns is None:
        return subprocess.run(
            [
                "/usr/bin/unshare",
                "--user",
                "--map-root-user",
                "--net",
                sys.executable,
                "-B",
                "-I",
                str(Path(__file__).resolve()),
                *sys.argv[1:],
                "--parent-netns",
                os.readlink("/proc/self/ns/net"),
            ]
        ).returncode
    if os.readlink("/proc/self/ns/net") == a.parent_netns:
        raise SystemExit("Private network namespace required")
    if not 60 <= a.warmup <= 900 or not 650 <= a.seconds <= 3600:
        raise SystemExit("Unaccelerated bounded observation required")
    a.output = a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["/usr/sbin/ip", "link", "set", "lo", "up"], check=True)
    cases = {
        "silent": [f"192.0.2.{i}" for i in range(11, 15)],
        "intermittent": [f"192.0.2.{i}" for i in range(21, 25)],
    }
    for addresses in cases.values():
        for ip in addresses:
            subprocess.run(["/usr/sbin/ip", "addr", "add", ip + "/32", "dev", "lo"], check=True)
    spec = importlib.util.spec_from_file_location("rt", ROOT / "work/marketdata_runtime.py")
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    policy = json.loads(Path("/etc/marketdata/runtime.json").read_text())["policy"]
    (a.output / "namespace.json").write_text(
        json.dumps(
            {
                "parent": a.parent_netns,
                "test": os.readlink("/proc/self/ns/net"),
                "uid_map": Path("/proc/self/uid_map").read_text(),
                "version": subprocess.check_output(["/usr/sbin/chronyd", "-v"], text=True),
            },
            indent=2,
        )
        + "\n"
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [
            pool.submit(run_case, case, ips, a.output, a.warmup, a.seconds, rt, policy)
            for case, ips in cases.items()
        ]
        for future in jobs:
            print(json.dumps(future.result()), flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
