import json
import subprocess

import pytest

from scripts import network_observer as observer


def test_failed_resolver_does_not_erase_successful_numeric_or_http_evidence(monkeypatch):
    def command(args, timeout=5):
        if args[0].endswith("ip"):
            return {
                "returncode": 0,
                "stdout": json.dumps([{"dev": "enp2s0", "gateway": "192.0.2.1"}]),
            }
        if args[0].endswith("getent"):
            return {"returncode": 2, "stdout": ""}
        if args[0].endswith("curl"):
            return {
                "returncode": 0,
                "stdout": json.dumps(
                    {"http_code": 200, "time_appconnect": 0.1, "remote_ip": "192.0.2.2"}
                ),
            }
        return {"returncode": 1, "stderr": "ICMP not answered"}

    monkeypatch.setattr(observer, "command", command)
    monkeypatch.setattr(observer, "link", lambda interface: {"ok": True, "interface": interface})
    monkeypatch.setattr(observer, "numeric_tcp", lambda address: {"ok": True, "address": address})
    result = observer.observe({"external_ip": "1.1.1.1", "expected_interface": "enp2s0"})
    assert result["status"] == "degraded"
    assert set(result["failed_probes"]) == {"rest_dns", "stream_dns", "gateway_icmp"}
    assert result["probes"]["rest_https"]["ok"]
    assert result["probes"]["external_numeric_tcp"]["ok"]
    assert "no root-cause verdict" in result["scope"]


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"returncode": None, "error": "Timeout"}, (False, False)),
        ({"returncode": 0, "stdout": '{"http_code":429}'}, (False, True)),
        ({"returncode": 0, "stdout": '{"http_code":200}'}, (True, True)),
        ({"returncode": 60, "stdout": '{"http_code":0}'}, (False, False)),
    ],
)
def test_http_errors_are_not_misreported_as_dns_or_success(monkeypatch, response, expected):
    monkeypatch.setattr(observer, "command", lambda *a, **k: dict(response))
    result = observer.exchange_probe(observer.HOSTS[0])
    assert (result["ok"], result.get("http_reachable", False)) == expected


def test_stream_probe_measures_https_transport_without_claiming_websocket_health(monkeypatch):
    def command(args, timeout):
        assert args[1] == "--disable" and "--head" in args
        assert "--connect-only" not in args
        assert timeout == 5
        return {"returncode": 0, "stdout": '{"http_code":400,"time_appconnect":0.1}'}

    monkeypatch.setattr(observer, "command", command)
    result = observer.exchange_probe(observer.HOSTS[1], transport_only=True)
    assert result["ok"] and result["http_reachable"]
    assert result["kind"] == "https_head_transport"


def test_command_timeout_is_bounded_evidence(monkeypatch):
    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 3
        raise subprocess.TimeoutExpired(args, 3)

    monkeypatch.setattr(observer.subprocess, "run", timeout)
    result = observer.resolve(observer.HOSTS[0])
    assert not result["ok"] and result["returncode"] is None
    assert "TimeoutExpired" in result["error"] and result["finished_at"]


@pytest.mark.parametrize(
    "routes", [[], [{"dev": "eth0"}, {"dev": "eth1"}], [{"dev": "../../bad"}], [{"dev": "lo"}]]
)
def test_missing_ambiguous_or_wrong_interface_is_not_healthy(monkeypatch, routes):
    monkeypatch.setattr(
        observer, "command", lambda *a, **k: {"returncode": 0, "stdout": json.dumps(routes)}
    )
    assert not observer.route("1.1.1.1", "enp2s0")["ok"]


def test_logs_append_and_latest_advances_even_for_network_failures(tmp_path, monkeypatch):
    profile = tmp_path / "profile.toml"
    output = tmp_path / "evidence"
    profile.write_text(f'version = 1\noutput_root = "{output}"\n')
    calls = iter(["2026-10-01T12:00:00+00:00", "2026-10-01T12:01:00+00:00"])

    def observe(config):
        return {
            "started_at": next(calls),
            "finished_at": "finished",
            "status": "degraded",
            "failed_probes": ["rest_dns"],
        }

    monkeypatch.setattr(observer, "observe", observe)
    assert observer.run(profile)["status"] == "degraded"
    assert observer.run(profile)["status"] == "degraded"
    records = [json.loads(line) for line in (output / "2026-10-01.jsonl").read_text().splitlines()]
    assert len(records) == 2 and records[0]["started_at"] != records[1]["started_at"]
    assert json.loads((output / "latest.json").read_bytes()) == records[1]


@pytest.mark.parametrize(
    "extra", ['external_ip = "example.com"', 'expected_interface = "../../eth0"', "unknown = true"]
)
def test_profile_rejects_ambiguous_or_non_numeric_settings(tmp_path, extra):
    profile = tmp_path / "profile.toml"
    profile.write_text(f'version = 1\noutput_root = "{tmp_path}"\n{extra}\n')
    with pytest.raises(ValueError):
        observer.load_profile(profile)
