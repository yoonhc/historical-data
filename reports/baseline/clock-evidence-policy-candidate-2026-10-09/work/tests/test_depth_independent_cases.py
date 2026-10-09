"""Frozen real incidents and the persistence boundary of live status reporting."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from marketdata import depth

FIXTURES = Path(__file__).parents[1] / "reports/baseline/depth-independent-2026-10-05"


def load(name):
    qualified = "depth_incident_audit_" + name
    if qualified not in sys.modules:
        spec = importlib.util.spec_from_file_location(qualified, FIXTURES / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[qualified]


@pytest.mark.parametrize(
    "cid,adoptions,old_remaining,new_remaining",
    [
        ("c595e79851ef4ced8e4194f22cf58b84", 74, 0, 0),
        ("54d28ea721ed41fab80928c064514159", 7, 0, 8),
    ],
)
def test_frozen_raw_incidents_without_production_book_engine(
    monkeypatch, cid, adoptions, old_remaining, new_remaining
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent result must not call the production book engine")

    monkeypatch.setattr(depth.Book, "apply", forbidden)
    monkeypatch.setattr(depth.History, "align", forbidden)
    monkeypatch.setattr(depth.Session, "compare", forbidden)
    monkeypatch.setattr(depth, "replay", forbidden)
    manifest = json.loads((FIXTURES / "inputs.json").read_bytes())
    case = next(c for c in manifest["cases"] if c["collection_id"] == cid)
    result = load("independent").inspect_case(case)
    expected = next(
        c for c in json.loads((FIXTURES / "results.json").read_bytes()) if c["collection_id"] == cid
    )
    assert result == expected
    assert len(result["adoptions"]) == adoptions and result["all_adoptions_common_equal"]
    side = "bids" if new_remaining == 0 else "asks"
    subset = result["subset_after_failure"][side]
    assert subset == {
        "premises_met": True,
        "values_subset": True,
        "old_remaining": old_remaining,
        "candidate_remaining": new_remaining,
    }
    assert result["original_partial_preserved"]
    if side == "asks":
        focus = result["focus"]
        assert focus["adopted_at_sequence"] == 27516
        assert focus["old"]["asks"]["levels"] == 651
        assert focus["candidate"]["asks"]["levels"] == 981
        change = result["failure"]["changes"][side]
        assert change["deleted_existing"] == 975 and change["added"] == 2
    else:
        assert result["failure"]["production_preserves_before_on_empty"]
        assert result["failure"]["before"]["bids"]["levels"] == 997
        assert result["failure"]["after_raw_reduction"]["bids"]["levels"] == 0


@pytest.mark.parametrize("remaining", [0, 8])
@pytest.mark.parametrize("delay", [0, 2])
def test_consumer_failure_lifecycle_with_early_failure_publication(tmp_path, remaining, delay):
    """Invalidity must be visible before event persistence and the full report commit."""
    result = load("consumer_probe").probe(tmp_path / "store", remaining, delay)
    observations = result["observations"]
    by_phase = {o["phase"]: o for o in observations}
    assert any(o["healthy"] for o in observations if o["phase"] == "after_flush")
    before = by_phase["failure_before_report_commit"]
    assert before["in_memory_sync_losses"] == 1
    assert before["in_memory_synchronized"] is False
    assert before["healthy"] is False and before["cli_exit"] == 2
    assert before["sync_losses"] == 1 and before["last_update_id"] == 102
    for phase in ("failure_before_event_persistence", "failure_after_synthetic_persistence_delay"):
        assert by_phase[phase]["healthy"] is False and by_phase[phase]["cli_exit"] == 2
    after = by_phase["failure_after_report_commit"]
    assert after["healthy"] is False and after["cli_exit"] == 2
    assert after["sync_losses"] == 1 and after["synchronized"] is False
    failed_epoch = after["epochs"][0]
    if remaining == 0:
        assert failed_epoch["error"] == "Known snapshot price range exhausted"
        assert failed_epoch["book"]["last_update_id"] == 102
        assert failed_epoch["book"]["top_complete"] is True
        assert failed_epoch["book"]["known_bid_levels"] == 100
    else:
        assert failed_epoch["error"] is None and failed_epoch["end_reason"] == "incomplete_top"
        assert failed_epoch["book"]["last_update_id"] == 103
        assert failed_epoch["book"]["top_complete"] is False
        assert failed_epoch["book"]["known_ask_levels"] == 8
    recovery = by_phase["before_recovery_snapshot"]
    assert recovery["healthy"] is False and recovery["cli_exit"] == 2
    assert result["final_book"]["synced"] and result["final_book"]["top_complete"]
    assert result["final_state"] == "partial" and result["complete"] is False
    assert result["replay_matches_live"] and result["replay_valid"] is False
    assert result["replay_cli_exit"] == 2
    assert result["messages"] == result["persisted_messages"]
