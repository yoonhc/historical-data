"""Run in the unchanged T05 interpreter: real parent readers with isolated I/O.

No service start, clock query, restic execution or operating catalog access.
Root-owned path checks and system bus answers are supplied by the fixture;
actual permissions and provenance in a real oneshot remain a root preflight.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_migration import prepared
import pinned
import run_system_archive
import transaction as tx


def clock_reader(t, monkeypatch):
    parent = Path(pinned.check_parents()['t05'])
    path = parent / 'packages/marketdata/src/marketdata/checkpoint_clock.py'
    spec = importlib.util.spec_from_file_location('bridge_original_checkpoint_clock', path)
    clock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clock)

    def mapped(value):
        path = Path(value)
        return path if path.is_relative_to(t.fs.root) else t.fs.path(str(path))

    monkeypatch.setattr(clock, 'Path', mapped)
    monkeypatch.setattr(clock, 'trusted', lambda path, **kwargs: mapped(path))
    monkeypatch.setattr(clock, 'boot_id', t.boot)
    return clock


def test_installed_t05_reader_accepts_commit_and_rollback_but_not_pending(tmp_path, monkeypatch):
    t = prepared(tmp_path)
    clock = clock_reader(t, monkeypatch)
    binding_path = '/etc/marketdata/checkpoint-clock-retirement/clock-binding.json'
    old_binding = t.fs.json(binding_path)
    with clock.Host(old_binding).lock():
        pass
    t.stage()
    with pytest.raises(clock.ClockHold, match='not proven closed'):
        with clock.Host(t.fs.json(binding_path)).lock():
            pass
    t.commit()
    with pytest.raises(clock.ClockHold, match='differs'):
        with clock.Host(old_binding).lock():
            pass
    with clock.Host(t.fs.json(binding_path)).lock() as host:
        assert host.boot == t.plan['boot_id']
    t.stage('rollback')
    t.commit()
    with clock.Host(old_binding).lock() as host:
        assert host.boot == t.plan['boot_id']


def test_installed_t05_shared_lock_conflicts_with_transition(tmp_path, monkeypatch):
    t = prepared(tmp_path)
    clock = clock_reader(t, monkeypatch)
    binding = t.fs.json('/etc/marketdata/checkpoint-clock-retirement/clock-binding.json')
    with t.fs.lock(), pytest.raises(clock.ClockHold, match='transition in progress'):
        with clock.Host(binding).lock():
            pass


@pytest.mark.parametrize('case', ['valid', 'manual', 'wrong_pid', 'wrong_timer', 'changed_job', 'wrong_stamp'])
def test_installed_archiver_checks_system_manager_job_provenance(monkeypatch, case):
    schedule = pinned.load_archive()
    original = schedule.service_context
    # Register restoration before binding; each case starts with the unmodified function.
    monkeypatch.setattr(schedule, 'service_context', original)
    run_system_archive.bind(schedule)
    invocation, job_id = 'a' * 32, 912
    real, mono = 1791806400000000, 123456789
    monkeypatch.setenv('INVOCATION_ID', invocation)
    for key in schedule.TRIGGER_KEYS[1:]:
        monkeypatch.delenv(key, raising=False)
    service_path = schedule.unit_bus_path(schedule.SERVICE)
    job_path = '/org/freedesktop/systemd1/job/' + str(job_id)
    calls, job_queries = [], []

    def output(argv, **kwargs):
        calls.append(argv)
        assert argv[1] == '--system'
        if argv[0] == 'systemctl':
            return ('Id=' + schedule.SERVICE + '\nType=oneshot\nInvocationID=' + invocation +
                    '\nMainPID=' + str(os.getpid() + (case == 'wrong_pid')))
        assert argv[0] == 'busctl'
        prop = argv[-1]
        values = {
            'Unit': ('(so)', [schedule.SERVICE, service_path]),
            'JobType': ('s', 'start'), 'State': ('s', 'running'),
            'ActivationDetails': ('a(ss)', [] if case == 'manual' else [
                ['trigger_unit', 'wrong.timer' if case == 'wrong_timer' else schedule.TIMER],
                ['trigger_timer_realtime_usec', str(real)],
                ['trigger_timer_monotonic_usec', str(mono)]]),
            'LastTriggerUSec': ('t', real + (case == 'wrong_stamp')),
            'LastTriggerUSecMonotonic': ('t', mono),
        }
        if prop == 'Job':
            job_queries.append(True)
            value = ('(uo)', [0, '/']) if case == 'changed_job' and len(job_queries) == 2 else (
                '(uo)', [job_id, job_path])
        else:
            value = values[prop]
        return json.dumps({'type': value[0], 'data': value[1]})

    monkeypatch.setattr(schedule.subprocess, 'check_output', output)
    context = schedule.service_context()
    assert context['scheduled'] is (case == 'valid')
    assert context['manager'] == 'system'
    assert context['module'] == schedule.__file__
    assert context['manager_adapter']['sha256'] == tx.sha(Path(run_system_archive.__file__).read_bytes())
    assert calls and all('--user' not in argv for argv in calls)
    assert original is not schedule.service_context
