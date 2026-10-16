"""Validate the actual probe dependency graph without starting any manager job."""
import json
import copy
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import handoff_common as c
import system_preflight as preflight
import pytest


def test_trial_dependencies_and_effective_commands_native(tmp_path):
    plan = json.loads((HERE / 'host-plan.json').read_bytes())
    prefix = 'md-t15-native-verification'
    release = c.BASE / 'candidate'
    ready = tmp_path / (prefix + '-ready.service')
    ready.write_text('[Service]\nType=oneshot\nUser=yoonhc\nExecStart=/usr/bin/true\nRemainAfterExit=yes\n')
    paths = [ready]
    for job in c.JOBS:
        raw = preflight.access_trial_unit(plan, job, prefix, release, tmp_path)
        deps = [line for line in raw.splitlines() if line.startswith(('Requires=', 'After='))]
        assert 'Requires=' + prefix + '-ready.service' in deps
        assert 'After=' + prefix + '-ready.service' in deps
        assert not any(c.WAITER in line for line in deps)
        # Effective last command set consists solely of help + access worker.
        commands, checks = [], []
        for line in raw.splitlines():
            if line.startswith('ExecStart='):
                value = line.split('=', 1)[1]
                commands.append(value) if value else commands.clear()
            if line.startswith('ExecStartPre='):
                value = line.split('=', 1)[1]
                checks.append(value) if value else checks.clear()
        assert not checks
        assert len(commands) == 2 and commands[0].endswith(' --help')
        assert '/trial_worker.py access ' in commands[1]
        assert 'ProtectClock=yes' in raw and 'User=yoonhc' in raw
        path = tmp_path / (prefix + '-' + job + '.service')
        path.write_text(raw)
        paths.append(path)
    # Empty final component preserves standard system unit search directories.
    env = dict(os.environ, SYSTEMD_UNIT_PATH=str(tmp_path) + ':')
    r = subprocess.run(['/usr/bin/systemd-analyze', 'verify', '--man=no', '--generators=no',
                        *map(str, paths)], capture_output=True, text=True, env=env, timeout=30)
    (HERE / 'preflight-native-verification.json').write_bytes(c.encode({
        'code': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr,
        'temporary_unit_count': len(paths), 'services_started': False,
        'input_sha256': c.sha((HERE / 'system_preflight.py').read_bytes())}))
    assert r.returncode == 0, r.stderr


def test_diagnostics_only_reads_registered_test_unit(monkeypatch):
    class Manager:
        def show(self, owner, name):
            return {'Id': name, 'LoadState': 'loaded'}
        def jobs(self, owner):
            return [{'unit': 'marketdata-backup.service'}, {'unit': 'md-t15-test-oi.service'}]
        def command(self, owner, action, name, *args, **kwargs):
            assert action == 'show' and name.startswith('md-t15-')
            return subprocess.CompletedProcess([], 0, 'Result=exit-code\nExecMainStatus=1\n', '')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert '--unit=md-t15-test-oi.service' in argv
        return subprocess.CompletedProcess(argv, 0, 'bounded trial failure', '')
    monkeypatch.setattr(preflight.subprocess, 'run', run)
    report = preflight.trial_diagnostics([('system', 'md-t15-test-oi.service', None, b'')], Manager())
    assert len(calls) == 1
    item = report['system:md-t15-test-oi.service']
    assert item['jobs'] == [{'unit': 'md-t15-test-oi.service'}]
    assert item['journal']['stdout'] == 'bounded trial failure'


def test_user_trial_keeps_manager_identity_without_group_setup():
    user = preflight.lifecycle_unit('/usr/bin/true', owner='user')
    system = preflight.lifecycle_unit('/usr/bin/true', owner='system')
    assert not any(line.startswith(('User=', 'Group=', 'SupplementaryGroups=')) for line in user.splitlines())
    assert 'User=yoonhc\n' in system
    assert system.replace('User=yoonhc\n', '') == user
    assert 'ProtectClock=yes\n' in user and 'NoNewPrivileges=yes\n' in user


def test_reuse_only_completed_parts_and_same_operating_state():
    old = json.loads((HERE / preflight.REUSE_REPORT).read_bytes())
    results, provenance = preflight.completed_probe_evidence(old['after'])
    assert old['passed'] is False  # whole failed report is NEVER called passed
    assert len(results) == 13 and 'system_drain' not in results
    assert provenance['failed_lifecycle_not_counted'] and not provenance['repeated']
    changed = copy.deepcopy(old['after'])
    changed['boot_id'] = 'different boot'
    with pytest.raises(c.Hold, match='current operating state'):
        preflight.completed_probe_evidence(changed)


def test_reuse_rejects_changed_original_report(monkeypatch):
    monkeypatch.setattr(preflight, 'REUSE_SHA256', '0' * 64)
    with pytest.raises(c.Hold, match='evidence changed'):
        preflight.completed_probe_evidence({})
