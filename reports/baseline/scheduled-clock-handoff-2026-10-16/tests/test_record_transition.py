"""Existing real A/old-B/A tests joined to the exact new configuration transaction.

systemd and Chrony are doubles; files, request/SQLite/JSONL and the old /opt CLI
are real. This is not an operating service transition.
"""
import copy
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
C = HERE.parent / "scheduled-clock-policy-storage-2026-10-16"
sys.path[:0] = [str(C / "candidate/base"), str(C / "candidate/base/tests"), str(C / "tests"), str(HERE)]
import pytest
from marketdata.store import Store
from test_wrapper_recovery import wrapper
import test_storage_recovery as recovery
from test_transition import lab, transaction, approval, rehash, unchanged_evidence
import transition as t


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "store")
    yield store
    store.close()


@pytest.mark.parametrize("job,boundary", [("oi-probe", "close"), ("network-observer", "seal")])
def test_config_rollback_old_body_reapply_same_candidate(lab, wrapper, monkeypatch, job, boundary):
    tx = transaction(lab)
    tx.stage(); tx.commit()
    original_unfinished = recovery.unfinished
    old_subprocess = recovery.subprocess.run
    transitions = []

    def pending_then_rollback(*args, **kwargs):
        result = original_unfinished(*args, **kwargs)
        inventory = t.pending_requests(wrapper.root / "requests")
        assert inventory[job]["requires_reconciliation"]
        # Explicit isolated rollback decision: preserve exact request and history.
        request = (wrapper.root / "requests" / job / "request.json").read_bytes()
        tx.stage("rollback"); tx.commit()
        unchanged_evidence(lab, "before")
        assert (wrapper.root / "requests" / job / "request.json").read_bytes() == request
        transitions.append("rollback-with-request-preserved")
        return result

    def old_cli_then_reapply(*args, **kwargs):
        assert transitions == ["rollback-with-request-preserved"]
        result = old_subprocess(*args, **kwargs)
        assert result.returncode in (0, 2)
        new = copy.deepcopy(lab[0])
        new["ledger_before"] = lab[1].json(t.LEDGER)
        rehash(new)
        again = t.Transaction(new, approval(new), lab[1], lab[2], boot=lambda: new["boot_id"])
        again.stage(); again.commit()
        unchanged_evidence(lab, "after")
        transitions.append("reapply-with-new-exact-plan")
        return result

    monkeypatch.setattr(recovery, "unfinished", pending_then_rollback)
    monkeypatch.setattr(recovery.subprocess, "run", old_cli_then_reapply)
    recovery.test_old_runtime_updates_latest_then_reapply_recovers_original(wrapper, monkeypatch, job, boundary)
    assert len(transitions) == 2
    assert wrapper.request(job)["state"] == "complete"
    unchanged_evidence(lab, "after")
