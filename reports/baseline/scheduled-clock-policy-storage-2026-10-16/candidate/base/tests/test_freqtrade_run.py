import json
import runpy
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from marketdata.domain import DataError, utc

runner = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/freqtrade_run.py"))


def test_backtest_range_includes_warmup_and_excludes_missing_segment():
    manifest = {"start": "2024-08-12T11:00Z", "end": "2026-09-22"}
    validate = runner["validate_timerange"]
    assert validate("20240814-20240901", manifest, 1) == (utc("2024-08-14"), utc("2024-09-01"))
    for value in ("20240813-20240901", "20240811-20240901", "20260901-20260923"):
        with pytest.raises(DataError, match="fit inside"):
            validate(value, manifest, 1)
    begin, end = utc("2025-09-01"), utc("2025-09-15")
    low, high = map(int, runner["freqtrade_timerange"](begin, end).split("-"))
    assert low == int(begin.timestamp())
    assert high == int(end.timestamp()) - 1


def test_resolved_compose_checks_actual_userdata_and_readonly_export(tmp_path, monkeypatch):
    service = {
        "image": runner["IMAGE"],
        "volumes": [
            {"target": "/freqtrade/user_data", "source": str(tmp_path / "user_data")},
            {"target": "/marketdata", "source": str(tmp_path / "export"), "read_only": True},
        ],
    }
    monkeypatch.setattr(
        runner["subprocess"],
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"services": {"freqtrade": service}})
        ),
    )
    check = runner["compose_preflight"]
    command = ["docker", "compose", "run", "--rm", "freqtrade"]
    assert check(command, {}, tmp_path, tmp_path / "export")["image"] == runner["IMAGE"]
    service["volumes"][0]["source"] = str(tmp_path / "ft_research/user_data")
    with pytest.raises(DataError, match="user_data mount"):
        check(command, {}, tmp_path, tmp_path / "export")
    service["volumes"][0]["source"] = str(tmp_path / "user_data")
    service["volumes"][1]["read_only"] = False
    with pytest.raises(DataError, match="read-only"):
        check(command, {}, tmp_path, tmp_path / "export")


def test_result_inspection_rejects_silently_shifted_backtest_period(tmp_path):
    result = {
        "trading_mode": "futures",
        "backtest_start": "2025-09-01 00:00:00",
        "backtest_end": "2025-09-14 23:55:00",
        "total_trades": 1,
        "trades": [{"funding_fees": 0.01}],
    }

    def write():
        with zipfile.ZipFile(tmp_path / "result.zip", "w") as zf:
            zf.writestr("result.json", json.dumps({"strategy": {"Example": result}}))

    write()
    check = runner["backtest_summary"]
    args = (tmp_path, "Example", "futures", utc("2025-09-01"), utc("2025-09-15"))
    assert check(*args)["trades_with_funding_fees"] == 1
    result["backtest_start"] = "2025-09-01 01:00:00"
    write()
    with pytest.raises(DataError, match="mode/start changed"):
        check(*args)
