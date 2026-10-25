"""Bounded local network evidence. Never reconfigure networking or touch market data."""

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from marketdata import job_clock as jc

HOSTS = ("fapi.binance.com", "fstream.binance.com")


def now():
    return datetime.now(UTC).isoformat()


def command(args, timeout=5):
    started, clock = now(), time.monotonic()
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        record = {
            "returncode": result.returncode,
            "stdout": result.stdout[:16000],
            "stderr": result.stderr[:2000],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        record = {"returncode": None, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "started_at": started,
        "finished_at": now(),
        "elapsed_seconds": round(time.monotonic() - clock, 6),
        **record,
    }


def route(external_ip, expected_interface):
    result = command(["/usr/bin/ip", "-j", "route", "get", external_ip], timeout=3)
    result.update(ok=False, interface=None, gateway=None)
    try:
        routes = json.loads(result.pop("stdout", ""))
        result["routes"] = routes
        if result["returncode"] != 0 or len(routes) != 1:
            return result
        item = routes[0]
        interface = item["dev"]
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
            raise ValueError("Invalid route interface")
        gateway = str(ipaddress.ip_address(item["gateway"])) if "gateway" in item else None
        result.update(
            interface=interface,
            gateway=gateway,
            expected_interface=expected_interface,
            ok=not expected_interface or interface == expected_interface,
        )
    except (KeyError, TypeError, ValueError) as exc:
        result["parse_error"] = str(exc)
    return result


def link(interface, root=Path("/sys/class/net")):
    result = {"interface": interface, "observed_at": now(), "ok": False}
    if interface is None:
        return {**result, "error": "No unambiguous selected route"}
    try:
        for name in ("carrier", "operstate"):
            result[name] = (root / interface / name).read_text().strip()
        result["ok"] = result["carrier"] == "1" and result["operstate"] in ("up", "unknown")
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def gateway_probe(route_result):
    gateway = route_result.get("gateway")
    if not gateway:
        return {"ok": None, "status": "not_applicable", "reason": "Route has no gateway"}
    result = command(
        ["/usr/bin/ping", "-n", "-c", "1", "-W", "2", "-I", route_result["interface"], gateway],
        timeout=3,
    )
    return {**result, "ok": result["returncode"] == 0, "gateway": gateway}


def resolve(host):
    result = command(["/usr/bin/getent", "ahosts", host], timeout=3)
    addresses = set()
    for line in result.pop("stdout", "").splitlines():
        try:
            addresses.add(str(ipaddress.ip_address(line.split()[0])))
        except (ValueError, IndexError):
            pass
    return {
        **result,
        "host": host,
        "addresses": sorted(addresses),
        "ok": result["returncode"] == 0 and bool(addresses),
    }


def numeric_tcp(address):
    started, clock = now(), time.monotonic()
    family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
    result = {"address": address, "port": 443, "started_at": started, "ok": False}
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect((address, 443))
            result.update(ok=True, local_address=sock.getsockname()[0])
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return {**result, "finished_at": now(), "elapsed_seconds": round(time.monotonic() - clock, 6)}


def exchange_probe(host, *, transport_only=False):
    url = f"https://{host}" + ("/" if transport_only else "/fapi/v1/ping")
    args = [
        "/usr/bin/curl",
        "--disable",
        "--silent",
        "--show-error",
        "--noproxy",
        "*",
        "--connect-timeout",
        "2",
        "--max-time",
        "4",
        "--output",
        "/dev/null",
        "--write-out",
        "%{json}",
    ]
    if transport_only:
        args.append("--head")
    result = command([*args, url], timeout=5)
    result.update(url=url, kind="https_head_transport" if transport_only else "http_ping", ok=False)
    try:
        details = json.loads(result.pop("stdout", ""))
        result["timings"] = {
            key: details.get(key)
            for key in (
                "http_code",
                "remote_ip",
                "remote_port",
                "local_ip",
                "time_namelookup",
                "time_connect",
                "time_appconnect",
                "time_total",
                "ssl_verify_result",
            )
        }
        result["http_reachable"] = bool(details.get("http_code", 0))
        result["ok"] = result["returncode"] == 0 and (
            details.get("time_appconnect", 0) > 0
            if transport_only
            else details.get("http_code") == 200
        )
    except (ValueError, TypeError) as exc:
        result["parse_error"] = str(exc)
    return result


def load_profile(path):
    config = tomllib.loads(path.read_text())
    if set(config) - {"version", "output_root", "expected_interface", "external_ip"}:
        raise ValueError("Unknown network observer profile setting")
    if config.get("version") != 1:
        raise ValueError("Expected network observer profile version 1")
    output = Path(config["output_root"])
    if not output.is_absolute():
        raise ValueError("output_root must be absolute")
    expected = config.get("expected_interface", "")
    if expected and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", expected):
        raise ValueError("Invalid expected_interface")
    return {
        "output_root": output,
        "expected_interface": expected,
        "external_ip": str(ipaddress.ip_address(config.get("external_ip", "8.8.8.8"))),
    }


def observe(config):
    observation = jc.active().point("network_observation_start") if jc.active() else None
    started = now()
    selected = route(config["external_ip"], config["expected_interface"])
    probes = {"selected_route": selected, "link": link(selected["interface"])}
    tasks = {
        "gateway_icmp": lambda: gateway_probe(selected),
        "external_numeric_tcp": lambda: numeric_tcp(config["external_ip"]),
        "rest_dns": lambda: resolve(HOSTS[0]),
        "stream_dns": lambda: resolve(HOSTS[1]),
        "rest_https": lambda: exchange_probe(HOSTS[0]),
        "stream_https_transport": lambda: exchange_probe(HOSTS[1], transport_only=True),
    }
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        pending = {name: executor.submit(fn) for name, fn in tasks.items()}
        for name, future in pending.items():
            try:
                probes[name] = future.result()
            except Exception as exc:
                probes[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    time_quality = {}
    if observation:
        try:
            end_point = jc.active().point("network_observation_end")
            time_quality = {
                "current_time_known": True,
                "start": observation.evidence,
                "end": end_point.evidence,
            }
        except jc.ClockDeferred as exc:
            time_quality = {
                "current_time_known": False,
                "start": observation.evidence,
                "reason": str(exc),
            }
    failed = [name for name, item in probes.items() if item["ok"] is False]
    return {
        "version": 1,
        **({"local_time_quality": time_quality} if observation else {}),
        "started_at": started,
        "finished_at": now(),
        "status": "degraded" if failed else "ok",
        "failed_probes": failed,
        "probes": probes,
        "context": {
            "uid": os.getuid(),
            "python": sys.executable,
            "script": str(Path(__file__).resolve()),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "invocation_id": os.environ.get("INVOCATION_ID"),
        },
        "scope": "one point sample; independent probes are not simultaneous; direct connections without proxies; no root-cause verdict; stream TLS is not a WebSocket health check",
    }


def run(profile):
    config = load_profile(profile)
    output = config["output_root"]
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (output / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        result = observe(config)
        result["profile_sha256"] = hashlib.sha256(profile.read_bytes()).hexdigest()
        encoded = (json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        journal_path = output / f"{result['started_at'][:10]}.jsonl"
        with journal_path.open("ab") as journal:
            offset = journal.tell()
            journal.write(encoded)
            journal.flush()
            os.fsync(journal.fileno())
        temporary = output / "latest.json.tmp"
        with temporary.open("wb") as latest:
            latest.write(encoded)
            latest.flush()
            os.fsync(latest.fileno())
        temporary.replace(output / "latest.json")
        return {
            "status": result["status"],
            "started_at": result["started_at"],
            "failed_probes": result["failed_probes"],
            "finished_at": result["finished_at"],
            "profile_sha256": result["profile_sha256"],
            "output_root": str(output),
            "observation_ref": {
                "kind": "network-jsonl-v1",
                "path": str(journal_path.absolute()),
                "offset": offset,
                "length": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            },
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    result = run(args.profile)
    jc.finished("network-observer", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
