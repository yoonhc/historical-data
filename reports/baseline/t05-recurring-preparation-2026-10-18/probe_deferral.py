"""Characterize installed automatic-run deferral; only temporary paths are used.

The installed execute() and ClockRetirement.locks() are real. Manager, clock,
authorization, preservation work and parent locks are doubles. This is not an
operating deletion, real lock-contention test or corrected-candidate test.
"""
import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

RELEASE = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
sys.path.insert(0, str(RELEASE))
from scripts import clock_retirement as safe

HERE = Path(__file__).resolve().parent
INSTANT = datetime(2026, 10, 18, 6, 40, 1, tzinfo=timezone.utc)
NEXT = datetime(2026, 10, 19, 6, 40, 1, tzinfo=timezone.utc)
PLAN = 'a' * 64


@contextmanager
def unlocked(*args):
    yield


class Host:
    def __init__(self, binding):
        pass

    @contextmanager
    def lock(self):
        yield self

    def query(self):
        raise AssertionError('The fake guard must not query a real clock')


class Guard:
    def __init__(self, *args):
        pass

    def start(self):
        return self

    def check(self, **kwargs):
        return {'ready': True}

    def close(self):
        pass


class PassedUnfinishedGuard(Exception):
    pass


def future_guard(settings, journals):
    agent = object.__new__(safe.ClockRetirement)
    agent.settings = settings
    agent.action = 'run'
    agent.day = NEXT.astimezone(safe.legacy.ZONE).date().isoformat()
    agent.report = {'run_id': 'next-evaluation'}
    agent.policy = SimpleNamespace()
    agent.p = SimpleNamespace(reports_root=settings.state_root / 'old-reports')
    agent.guard = Guard()
    agent.manual_plan = None
    agent.journals = lambda: journals
    agent.a = SimpleNamespace(state={})
    with (patch.object(safe.retirement.Retirement, 'locks', unlocked),
          patch.object(safe, 'authorize', return_value={}),
          patch.object(safe, 'now', return_value=NEXT),
          patch.object(safe.archive, 'discover', side_effect=PassedUnfinishedGuard)):
        try:
            with agent.locks():
                raise AssertionError('Unexpected passage through fake discovery')
        except safe.legacy.Deferred as exc:
            return {'reached_discovery': False, 'reason': exc.reason,
                    'attention': exc.attention, 'detail': str(exc)}
        except PassedUnfinishedGuard:
            return {'reached_discovery': True}


def case(root, when):
    root.mkdir()
    binding = root / 'binding.json'
    binding.write_text('{}')
    settings = SimpleNamespace(state_root=root / 'state', retention_policy=root / 'policy',
                               clock_binding_file=binding)

    class Agent:
        def __init__(self, *args):
            self.root = settings.state_root / 'retention'

        def plan(self):
            if when == 'planning_lock':
                raise safe.archive.Busy('synthetic planning lock contention')
            return {'plan_id': PLAN, 'candidates': [{'synthetic': True}]}

        def apply(self, plan_id):
            raise safe.archive.Busy('synthetic apply lock contention before any journal')

    with (patch.object(safe.retirement.Policy, 'load', return_value=SimpleNamespace(archive_profile=root / 'profile')),
          patch.object(safe.archive.Profile, 'load', return_value=SimpleNamespace()),
          patch.object(safe, 'authorize', return_value={}),
          patch.object(safe, 'installed_service'),
          patch.object(safe, 'system_context', return_value={'service_verified': True, 'scheduled': True,
                                                          'trigger_at': '2026-10-18T06:40:00+00:00'}),
          patch.object(safe.clock, 'Guard', Guard),
          patch.object(safe, 'now', return_value=INSTANT),
          patch.object(safe, 'ClockRetirement', Agent)):
        report = safe.execute(settings, 'run', host_factory=Host)
    persisted = json.loads(Path(report['report']).read_bytes())
    assert persisted['state'] == 'deferred' and persisted['exit_code'] == 75
    assert persisted['deleted_this_run'] == 0
    assert not list(settings.state_root.rglob('journals/*.json'))
    following = future_guard(settings, [])
    complete = future_guard(settings, [{'plan_id': PLAN, 'status': 'complete'}])
    unfinished = future_guard(settings, [{'plan_id': PLAN, 'status': 'applying'}])
    assert following['reached_discovery'] == (when == 'planning_lock')
    assert complete['reached_discovery']
    assert unfinished['reason'] == 'unfinished_retirement' and unfinished['attention']
    return {'injected_at': when,
            'persisted_report': {k: persisted[k] for k in ('state', 'exit_code', 'deletion_attempted',
                                                        'deleted_this_run', 'needs_attention', 'reason', 'plan_id')},
            'journal_count': 0, 'next_evaluation': following,
            'matching_complete_journal_control': complete,
            'unfinished_journal_control': unfinished}


def main():
    with tempfile.TemporaryDirectory(prefix='t05-deferral-', dir='/tmp') as temp:
        cases = [case(Path(temp) / name, name) for name in ('planning_lock', 'apply_lock')]
    files = [RELEASE / 'scripts' / name for name in ('clock_retirement.py', 'retire_checkpoints.py')]
    result = {'characterization_passed': True, 'implementation_changed': False,
              'operating_writes': False, 'clock_or_manager_control': False,
              'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              'python': sys.version, 'cases': cases,
              'limits': __doc__}
    (HERE / 'deferral-probe.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
