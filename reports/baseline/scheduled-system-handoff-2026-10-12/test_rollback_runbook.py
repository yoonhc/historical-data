"""Partial rollback's retained request record and explicit operator replay.

The executor replays requests it cancels in the current direction. The runbook
also checks the prior apply record, so requests cancelled before any system
start are not silently left until their next regular timer (notably backup).
"""
import json

import pytest

from test_handoff import make
import handoff_common as c


def test_partial_apply_requests_remain_reviewable_after_rollback(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    e.fault = lambda point: (_ for _ in ()).throw(InterruptedError()) if point == 'phase:drained' else None
    with pytest.raises(InterruptedError):
        e.run()
    original_bytes = e.fs.path(e.path('apply.json')).read_bytes()
    apply = json.loads(original_bytes)
    assert len(apply['cancelled']) == 2 and not apply['start_requests']
    e.fault = lambda _: None
    e.run('rollback')
    assert e.fs.path(e.path('apply.json')).read_bytes() == original_bytes
    # No assumption that the rollback invocation copied the old apply queue.
    # Read both records, check actual idle state and lack of a start request,
    # then explicitly reevaluate exactly the named original service requests.
    rollback = e.fs.json(e.path('rollback.json'))
    requested = set(rollback['start_requests'])
    pending = [key for key in apply['cancelled']
               if key not in requested and ('system:' + key.split(':', 1)[1]) not in apply['start_requests']]
    assert len(pending) == 2
    for key in pending:
        owner, name = key.split(':', 1)
        assert owner == 'user' and name in c.SERVICES
        assert e.m.states(owner, [name])[name]['classification'] == 'idle'
        e.m.command(owner, 'start', '--no-block', name)
    started = [n for owner, action, names in e.m.trace
               if owner == 'user' and action == 'start' for n in names if n in c.SERVICES]
    assert sorted(started) == sorted(key.split(':', 1)[1] for key in pending)
