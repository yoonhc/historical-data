"""Count real chronyc packets against an inert /tmp socket that drops all replies.

No chronyd, real command socket, external time source, or clock syscall is used.
The candidate case invokes Host.step, not a duplicate of its command construction.
"""

import argparse
import hashlib
import importlib.util
import json
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("boot", Path(__file__).with_name("clock_gate.py"))
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


def test(binary):
    root = Path(tempfile.mkdtemp(prefix="md-chronyc-wire-"))
    evidence = {
        "root": str(root),
        "binary": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "version": subprocess.check_output([str(binary), "-v"], text=True).strip(),
        "host_clock_control": False,
        "real_daemon_used": False,
        "cases": [],
    }
    identity = {"pid": 123, "invocation_id": "isolated"}
    # Only provider discovery is a fixture. The subprocess/socket path is real.
    boot.rt.provider_identity = lambda _: identity
    for name in ("previous_candidate", "current_candidate"):
        directory = root / name
        directory.mkdir()
        path = directory / "test.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(path))
        server.settimeout(0.05)
        stop, packets = threading.Event(), []
        started = time.monotonic()

        def receive():
            while not stop.is_set():
                try:
                    data, peer = server.recvfrom(8192)
                except socket.timeout:
                    continue
                packets.append(
                    {
                        "seconds": time.monotonic() - started,
                        "command": int.from_bytes(data[4:6], "big"),
                        "attempt": int.from_bytes(data[6:8], "big"),
                        "client_socket": peer,
                        "client_exists": Path(peer).exists(),
                        "packet_hex": data.hex(),
                    }
                )

        thread = threading.Thread(target=receive)
        thread.start()
        outcome = None
        try:
            if name == "previous_candidate":
                boot.rt.command([str(binary), "-n", "-h", str(path), "makestep"])
            else:
                boot.Host({"chrony_socket": str(path)}, {"chronyc": str(binary)}).step(identity)
        except (boot.rt.NotReady, subprocess.TimeoutExpired) as exc:
            outcome = type(exc).__name__
        finally:
            stop.set()
            thread.join()
            server.close()
        assert outcome and packets and all(p["command"] == 43 for p in packets)
        assert all(p["client_exists"] for p in packets)
        if name == "previous_candidate":
            assert len(packets) >= 2
        else:
            assert len(packets) == 1 and packets[0]["attempt"] == 0
        evidence["cases"].append(
            {"name": name, "outcome": outcome, "requests": len(packets), "packets": packets}
        )
    evidence["passed"] = True
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chronyc", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = test(args.chronyc.resolve())
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {"passed": result["passed"], "requests": [c["requests"] for c in result["cases"]]}
        )
    )
