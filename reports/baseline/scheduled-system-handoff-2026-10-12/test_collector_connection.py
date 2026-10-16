"""Real installed supervisor/gate with isolated transition, clocks and children.

No operating collector is started, stopped, probed or modified.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
BASE = HERE.with_name('scheduled-system-migration-2026-10-12')
sys.path.insert(0, str(BASE))
from test_migration import prepared, unchanged_clock
import test_migration
test_migration.PLAN = json.loads((HERE / 'configuration-plan.json').read_bytes())
import transaction as tx

PARENT = Path('/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325')
sys.path[:0] = [str(PARENT / 'tests'), str(PARENT / 'packages/marketdata/src')]
from test_clock_evidence_policy import cr, clock, stamp, Timer, Memory


def reader(t, profile, monkeypatch):
    real_spec = cr.importlib.util.spec_from_file_location
    def spec(name, path, **kwargs):
        result = real_spec(name, path, **kwargs)
        if name != 'bootstrap_gate':
            return result
        original = result.loader
        def execute(gate):
            original.exec_module(gate)
            ledger, check = gate.Ledger, gate.check_gate
            gate.read_profile = lambda _: (t.fs.json(tx.PROFILE), profile)
            gate.trusted = lambda *a, **k: None  # authority separately tested as root
            gate.Ledger = lambda _: ledger(t.fs.path(tx.LEDGER).parent)
            gate.check_gate = lambda p, r: check(p, r, ledger=gate.Ledger(None),
                checker=lambda _: clock(profile, 1), current_boot=t.plan['boot_id'])
        result.loader = SimpleNamespace(create_module=original.create_module, exec_module=execute)
        return result
    monkeypatch.setattr(cr.importlib.util, 'spec_from_file_location', spec)
    monkeypatch.setattr(cr.rt, 'boot_id', t.boot)
    return lambda: cr.bootstrap_receipt(Path(tx.PROFILE), profile)


@pytest.mark.parametrize('stream', ['depth', 'aggtrade'])
@pytest.mark.parametrize('direction', ['apply', 'rollback'])
def test_existing_supervisor_crosses_pending_but_new_start_waits(tmp_path, monkeypatch, stream, direction):
    t = prepared(tmp_path)
    profile = cr.rt.load_profile(PARENT / 'integrations/runtime/profile.example.json')
    profile.update(data_root=str(tmp_path / 'store'), evidence_root=str(tmp_path / 'evidence'))
    read = reader(t, profile, monkeypatch)
    receipt = read()
    timer, evidence, starts, rejected = Timer(), Memory(), [], []
    class Child:
        returncode = 0
        def __init__(self):
            self.pid = 100 + len(starts)
            self.end = timer.t + 3
        def poll(self):
            return 0 if timer.t >= self.end else None
    def spawn(*args, **kwargs):
        starts.append(json.loads(kwargs['env']['MARKETDATA_CLOCK_CONTEXT']))
        if len(starts) == 1:
            t.stage()
            with pytest.raises(Exception, match='ledger|state|profile'):
                read()  # independent supervisor restart cannot accept pending
            rejected.append(True)
        else:
            assert t.fs.json(tx.LEDGER)['schema'] == 2
        return Child()
    monkeypatch.setattr(cr.rt, 'process_start', lambda pid: str(pid))
    code = cr.supervise(profile, stream, evidence, timer, receipt,
        checker=lambda _: clock(profile, timer.t, age=1 if timer.t < 10 else 900),
        env_check=lambda _: 'isolated', spawn=spawn, stamp=lambda: stamp(timer.t),
        monotonic=lambda: timer.t, max_children=2)
    assert code == 0 and len(starts) == 2 and rejected == [True]
    assert starts[0]['run_id'] == starts[1]['run_id']
    assert starts[0]['child_id'] != starts[1]['child_id']
    rows = [r for r in evidence.rows if r['state'] == 'starting_child']
    assert rows[1]['decision']['reference_stale'] and rows[1]['decision']['raw_start_allowed']
    assert any(r['state'] == 'between_sessions' for r in evidence.rows)
    if direction == 'rollback':
        t.stage('rollback')
    t.commit()
    fresh_receipt = read()
    assert fresh_receipt['ready'] and fresh_receipt['phase'] == 'closed'
    assert fresh_receipt['step_attempts'] == receipt['step_attempts']
    unchanged_clock(t.plan['ledger_before'], t.fs.json(tx.LEDGER))
