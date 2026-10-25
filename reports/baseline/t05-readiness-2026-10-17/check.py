"""Read T05 prerequisites; no registration, plan application or service control."""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RELEASE = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
sys.path[:0] = [str(RELEASE), str(RELEASE / 'packages/marketdata/src')]
from marketdata import checkpoint_evidence as evidence
from scripts import archive_backups as archive
from scripts import clock_retirement as retirement
from scripts import checkpoint_schedule as schedule
from scripts import retirement_clock as clock

HERE = Path(__file__).resolve().parent
SETTINGS = Path('/etc/marketdata/checkpoint-clock-retirement/settings.toml')
UNITS = ['chrony.service', 'marketdata-collector@depth.service',
         'marketdata-collector@aggtrade.service', 'marketdata-backup.service',
         'marketdata-backup.timer', 'marketdata-restic-archive.service',
         'marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer',
         'marketdata-checkpoint-retirement-preview.service']
PROPERTIES = ('Id,LoadState,ActiveState,SubState,MainPID,InvocationID,Result,ExecMainStatus,'
              'ExecMainStartTimestamp,ExecMainExitTimestamp,UnitFileState,NextElapseUSecRealtime')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(name, value):
    raw = value if isinstance(value, bytes) else (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()
    (HERE / name).write_bytes(raw)
    return {'path': name, 'sha256': sha(raw), 'bytes': len(raw)}


def command(args, timeout=30):
    completed = subprocess.run(args, capture_output=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(f'Query failed ({completed.returncode}): {completed.stderr.decode()}')
    return completed.stdout


def host():
    raw = command(['systemctl', 'show', *UNITS, '--property=' + PROPERTIES]).decode()
    units = {}
    for block in raw.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        units[values['Id']] = values
    paths = [Path(manager) / unit for manager in ('/etc/systemd/system', '/home/yoonhc/.config/systemd/user')
             for unit in ('marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer')]
    masks = {str(p): os.readlink(p) if p.is_symlink() else None for p in paths}
    return {'units': units, 'masks': masks, 'boot_id': clock.boot_id()}


def main():
    before = host()
    save('host-before.json', before)
    assert all(v == '/dev/null' for v in before['masks'].values())
    settings = retirement.Settings.load(SETTINGS)
    policy = retirement.retirement.Policy.load(settings.retention_policy)
    profile = archive.Profile.load(policy.archive_profile)
    proposal = retirement.proposal(settings)
    save('current-disabled-proposal.json', proposal)
    installed_grant = json.loads(settings.authorization_file.read_bytes())
    assert installed_grant['enabled'] is False
    assert json.loads(settings.manual_authorization_file.read_bytes())['enabled'] is False
    try:
        retirement.authorize(settings, policy, profile, 'preview')
        scope_check = {'allowed': True}
    except schedule.Deferred as exc:
        scope_check = {'allowed': False, 'reason': exc.reason, 'detail': str(exc)}
    scope_check.update(current_scope_id=proposal['scope_id'], installed_scope_id=installed_grant['scope_id'],
                       installed_binding=installed_grant['scope'].get('clock_binding'),
                       current_binding=proposal['scope']['clock_binding'], proposal_installed=False)
    root = profile.state_root / profile.expected_repository_id
    path = root / profile.archive_scope / 'state.json'
    with (root / 'archive.lock').open('rb') as handle:
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        state_raw = path.read_bytes()
        state = json.loads(state_raw)
        save('archive-state.json', state_raw)
        record = max(state['checkpoints'].values(), key=lambda r: r['entry']['completed_at'])
        entry, stored, check = record['entry'], record['stored'], state['repository_check']
        bid, sid = entry['backup_id'], stored['snapshot_id']
        assert check['ok'] and check['read_data'] and check['receipt']['returncode'] == 0
        assert sid in check['snapshot_ids'] and record['input_verified_at'] and not record.get('error')
        assert not state.get('active_operation') and state['last_operation']['ok']
        cp = Path(entry['path'])
        assert sha((cp / 'backup.json').read_bytes()) == entry['manifest_sha256']
        local_bundle = (cp / evidence.SIDECAR).read_bytes()
        assert sha(local_bundle) == stored['checkpoint_evidence']['sha256']
        raw = command([str(profile.restic_binary), '-r', str(profile.restic_repository),
                       '--password-file', str(profile.restic_password_file), '--no-cache', '--no-lock',
                       'dump', sid, '/' + evidence.SIDECAR], timeout=45)
        assert raw == local_bundle
        saved_bundle = save('explicit-snapshot-bundle.json', raw)
        bundle = evidence.decode_bundle(raw)
        assert bundle['object_ids'] == stored['checkpoint_evidence']['object_ids']
        graph = evidence.Graph(bundle['bundle']['objects'], profile.identity)
        assert set(graph.baselines) == {'5ec056db51157f58a4537cabf374eee65148dfd901007c1138c1cba481b6cf0b'}
        baseline_ids = {x['backup_id'] for b in graph.baselines.values() for x in b['entries']}
        entries = []
        for checkpoint in graph.checkpoints:
            r = state['checkpoints'][checkpoint]
            assert r['input_verified_at'] and r['stored']['snapshot_id'] in check['snapshot_ids']
            assert not r.get('error') and (Path(r['entry']['path']) / 'backup.json').is_file()
            entries.append(r['entry'])
        maintenance = (profile.store_root / 'state/maintenance-daily-latest.json').read_bytes()
        save('maintenance-latest.json', maintenance)
        day = datetime.now(schedule.ZONE).date().isoformat()
        try:
            daily = {'allowed': True, 'evidence': schedule.daily_evidence(profile, state, day)}
        except schedule.Deferred as exc:
            daily = {'allowed': False, 'reason': exc.reason, 'detail': str(exc), 'date_kst': day}
        binding = json.loads(settings.clock_binding_file.read_bytes())
        with clock.Host(binding).lock() as reader:
            guard = clock.Guard(reader.query, HERE / 'clock-observation.jsonl').start()
            try:
                observation = guard.check(fresh=True)
                keep, conditional, proofs = evidence.select(
                    entries, graph, observation['lower_utc_ns'],
                    keep_latest=policy.keep_latest, min_age_hours=policy.min_age_hours)
            finally:
                guard.close()
        save('clock-observation.json', observation)
        save('time-and-order-only.json', {'not_a_retirement_plan': True,
             'retained': keep, 'conditional_candidates': conditional, 'proofs': proofs,
             'full_retirement_audit_performed': False})
        save('previous-full-check.stdout', Path(check['receipt']['stdout']).read_bytes())
        assert path.read_bytes() == state_raw
    after = host()
    save('host-after.json', after)
    assert after['masks'] == before['masks'] and after['boot_id'] == before['boot_id']
    for unit in UNITS[:3]:
        for field in ('MainPID', 'InvocationID', 'ActiveState'):
            assert after['units'][unit][field] == before['units'][unit][field]
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'assessment_completed': True,
              'formal_preview_ready': False, 'formal_preview_run': False,
              'backup_id': bid, 'snapshot_id': sid, 'latest_completed_at': entry['completed_at'],
              'full_check_finished_at': check['finished_at'], 'snapshot_bundle': saved_bundle,
              'objects': len(graph.objects), 'baseline_checkpoints': len(baseline_ids),
              'post_baseline_successors': len(set(graph.checkpoints) - baseline_ids),
              'time_order_conditional_candidates': len(conditional), 'time_order_retained': len(keep),
              'time_order_assessment_is_not_deletion_authority': True,
              'current_uncertainty_seconds': observation['uncertainty_seconds'],
              'current_observation_lower_utc_ns': observation['lower_utc_ns'],
              'scope_check': scope_check, 'daily_check': daily,
              'automatic_retirement': 'masked', 'grant_writes': False, 'registry_writes': False,
              'checkpoint_deletion': False, 'new_backup': False, 'new_repository_check': False,
              'bulk_restore': False, 'protected_processes_unchanged': True,
              'limitations': ['Time/order classification uses archived evidence and known local paths; '
                              'full current filesystem inventory and retained-data integrity audit were not run.',
                              'The latest backup predates the T16 deployment; this is not validation of a new T16 backup.']}
    save('result.json', result)
    save('evidence-manifest.json', {'files': {p.name: sha(p.read_bytes()) for p in sorted(HERE.iterdir())
                                           if p.is_file() and p.name != 'evidence-manifest.json'}})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
