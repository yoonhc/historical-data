"""Actual OI rows must survive report -> result seal -> request persistence."""

# ruff: noqa: F811 -- pytest imports this fixture and injects it by name.

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest
from marketdata import cli
from marketdata import job_clock as jc
from marketdata.domain import json_bytes, utc
from test_oi_quality import populate
from test_wrapper_recovery import wrapper  # noqa: F401


def populated_quality(wrapper, monkeypatch):
    begin, end = populate(wrapper.store, missing_rest=True)
    wrapper.samples.wall = (end + dt.timedelta(hours=9)).timestamp()
    observed = []
    original = cli.quality_report

    def quality(*args, **kwargs):
        value = original(*args, **kwargs)
        observed.append(value)
        return value

    monkeypatch.setattr(cli, "quality_report", quality)
    return observed


def decimals(value):
    if isinstance(value, Decimal):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from decimals(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from decimals(child)


def test_populated_quality_commits_precise_numbers_and_terminal_findings(wrapper, monkeypatch):
    observed = populated_quality(wrapper, monkeypatch)
    assert wrapper.run() == 2
    assert list(decimals(observed[0]))  # Empty-store tests missed the deployed failure.
    request = wrapper.request()
    assert request["state"] == "complete"
    assert request["disposition"]["reason"] == "report_written_with_findings"
    value = request["work_result"]
    assert json.loads(Path(value["files"][0]["path"]).read_bytes()) == value["result"]
    assert json.loads(json_bytes(observed[0])) == value["result"]
    assert not request["attempt"]["errors"]


@pytest.mark.parametrize("boundary", ["seal", "close"])
def test_decimal_result_failure_is_saved_and_resumed_without_recomputing(
    wrapper, monkeypatch, boundary
):
    observed = populated_quality(wrapper, monkeypatch)
    if boundary == "seal":
        original = wrapper.runner.load_contract

        def broken_contract():
            contract = original()
            contract.seal_result = lambda value: (_ for _ in ()).throw(OSError("seal unavailable"))
            return contract

        monkeypatch.setattr(wrapper.runner, "load_contract", broken_contract)
    else:
        original = wrapper.context_class.close

        def broken_close(context):
            original(context)
            raise jc.ClockFailure("close unavailable")

        monkeypatch.setattr(wrapper.context_class, "close", broken_close)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    failed = wrapper.request()
    assert failed["state"] == "finalization_failed" and failed["exit_code"] == 1
    assert failed.get("unsealed_result") if boundary == "seal" else failed.get("work_result")
    assert list(decimals(observed[0]))
    if boundary == "seal":
        monkeypatch.setattr(wrapper.runner, "load_contract", original)
    else:
        monkeypatch.setattr(wrapper.context_class, "close", original)
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *args: pytest.fail("report already stored")
    )
    assert wrapper.run() == 2
    after = wrapper.request()
    assert after["request_id"] == failed["request_id"] and after["pins"] == failed["pins"]
    assert after["state"] == "complete"
    assert after["attempt"]["work"]["recovered_committed_result"]
    assert len(observed) == 1


@pytest.mark.parametrize("which", ["runner", "contract"])
def test_precision_and_existing_json_bytes_are_preserved(wrapper, which):
    encoder = wrapper.runner if which == "runner" else wrapper.runner.load_contract()
    value = {
        "quantity": Decimal("12345678901234567890.123456789012345678"),
        "small": Decimal("0.000000000000000001"),
        "zero": Decimal("0.000"),
        "at": utc("2026-10-16"),
        "day": dt.date(2026, 10, 16),
    }
    result = json.loads(encoder.encode(value))
    assert result["quantity"] == str(value["quantity"])
    assert result["small"] == str(value["small"]) and result["zero"] == "0.000"
    ordinary = {"at": value["at"], "list": [1, None, "한글", 0.12]}
    assert encoder.encode(ordinary) == (
        json.dumps(ordinary, sort_keys=True, default=lambda v: v.isoformat()).encode() + b"\n"
    )
    with pytest.raises(TypeError, match="Unsupported request value"):
        encoder.encode({"not_a_result": object()})


def test_original_encoder_fails_on_the_same_actual_quality_result(wrapper, monkeypatch):
    observed = populated_quality(wrapper, monkeypatch)
    assert wrapper.run() == 2
    with pytest.raises(AttributeError, match="isoformat"):
        json.dumps(observed[0], sort_keys=True, default=lambda value: value.isoformat())


def test_existing_operating_report_is_a_valid_reviewed_result_without_requery(wrapper):
    # Read captured bytes only. This does not edit/reconcile the operating request.
    folder = Path(__file__).resolve().parent / "fixtures/decimal-recovery"
    request = json.loads((folder / "request.json").read_bytes())
    report = json.loads((folder / "report.json").read_bytes())
    contract = wrapper.runner.load_contract()
    result = contract.seal_result(
        {
            "job": "oi-quality",
            "result": report,
            "files": [str(folder / "report.json")],
            "records": [],
        }
    )
    disposition = contract.classify("oi-quality", 2, result, request["pins"], request["arguments"])
    assert disposition["state"] == "complete"
    assert disposition["reason"] == "report_written_with_findings"
    assert request["state"] == "pending" and "work_result" not in request
    # Classification alone is not authorization to bind a missing request result.
    assert json.loads((folder / "request.json").read_bytes()) == request
