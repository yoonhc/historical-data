"""Exercise real Chrony CSV over loopback with -x (no host clock control)."""

import importlib.util
import json
import os
import pwd
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    binary_root = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        "rt", Path(__file__).with_name("marketdata_runtime.py")
    )
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    work = Path(tempfile.mkdtemp(prefix="md-chrony-no-clock-"))
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(0.1)
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    control_port = probe.getsockname()[1]
    probe.close()
    stop = threading.Event()

    def ntp_time():
        value = time.time() + 2208988800
        return struct.pack("!II", int(value), int((value % 1) * 2**32))

    def answer():
        while not stop.is_set():
            try:
                request, address = server.recvfrom(1024)
            except socket.timeout:
                continue
            if len(request) < 48:
                continue
            response = bytearray(48)
            response[:4] = bytes([0x24, 1, 0, 236])
            response[8:12] = struct.pack("!I", 66)
            response[12:16] = b"TEST"
            response[16:24] = ntp_time()
            response[24:32] = request[40:48]
            response[32:40] = ntp_time()
            response[40:48] = ntp_time()
            server.sendto(response, address)

    config = work / "chrony.conf"
    config.write_text(
        f"server 127.0.0.1 port {server.getsockname()[1]} iburst minpoll 0 maxpoll 0\n"
        f"pidfile {work}/chronyd.pid\nport 0\ncmdport {control_port}\n"
        "bindcmdaddress 127.0.0.1\nbindcmdaddress /\n"
    )
    argv = [
        str(binary_root / "usr/sbin/chronyd"),
        "-x",
        "-U",
        "-u",
        pwd.getpwuid(os.getuid()).pw_name,
        "-n",
        "-f",
        str(config),
    ]
    thread = threading.Thread(target=answer)
    thread.start()
    result = {
        "scope": "synthetic_loopback_ntp_no_clock_control",
        "uid": os.getuid(),
        "daemon_argv": argv,
        "host_accuracy_validated": False,
    }
    child = None
    try:
        with (output / "daemon.log").open("w") as log:
            child = subprocess.Popen(argv, stdout=log, stderr=log)
            deadline = time.monotonic() + 25
            samples = []
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError((output / "daemon.log").read_text())
                query = subprocess.run(
                    [
                        str(binary_root / "usr/bin/chronyc"),
                        "-n",
                        "-c",
                        "-h",
                        "127.0.0.1",
                        "-p",
                        str(control_port),
                        "tracking",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=3,
                )
                if query.returncode == 0:
                    parsed = rt.tracking(query.stdout)
                    samples.append(parsed)
                    if parsed["leap"] == "Normal":
                        break
                time.sleep(0.2)
            else:
                raise RuntimeError("Synthetic source did not synchronize")
            proc = Path(f"/proc/{child.pid}/status").read_text()
            cap = next(x for x in proc.splitlines() if x.startswith("CapEff:"))
            assert int(cap.split()[1], 16) == 0
            result.update(
                capability_effective=cap,
                parsed_samples=samples,
                clock_control_disabled=True,
                ok=True,
            )
    finally:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=5)
        stop.set()
        thread.join(timeout=2)
        server.close()
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "parsed_samples"}))


if __name__ == "__main__":
    main()
