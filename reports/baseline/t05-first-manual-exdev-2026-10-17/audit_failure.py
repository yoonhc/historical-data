"""Read operating evidence; write copies and a manifest only beside this script."""
import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
PLAN = '31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08'
RUN = '5d7deaf59b1f4c99847a732327ce77af'
ATTEMPT = '94319a1f731c4812bce709f6722fefbe'
STATE = Path('/var/lib/marketdata-checkpoint-retirement')
CONFIG = Path('/etc/marketdata/checkpoint-clock-retirement')
UNIT = Path('/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    spec = importlib.util.spec_from_file_location('reviewed_scope', BASE / 't05-disabled-scope-2026-10-17/refresh_scope.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    before = json.loads((BASE / 't05-formal-preview-2026-10-17/after.json').read_bytes())
    scope_plan = json.loads((BASE / 't05-disabled-scope-2026-10-17/plan.json').read_bytes())
    plan = json.loads((STATE / 'retention/plans' / (PLAN + '.json')).read_bytes())
    journal_path = STATE / 'retention/journals' / (PLAN + '.json')
    journal = json.loads(journal_path.read_bytes())
    run_dir = STATE / 'runs' / RUN
    run = json.loads((run_dir / 'run.json').read_bytes())
    root = Path('/var/lib/marketdata-clock/t05-manual-applications') / PLAN / ATTEMPT
    manifest = {}

    def preserve(path, relative):
        raw = path.read_bytes()
        target = HERE / 'evidence' / relative
        if relative.parts[:2] == ('run', 'commands') and len(raw) > 131072:
            manifest[str(path)] = {'copy': None, 'sha256': sha(raw), 'bytes': len(raw),
                                   'storage': 'original run command output retained; hash indexed without large duplicate'}
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert target.read_bytes() == raw, 'Evidence copy differs'
        else:
            with target.open('xb') as f:
                f.write(raw)
        manifest[str(path)] = {'copy': str(target.relative_to(HERE)), 'sha256': sha(raw), 'bytes': len(raw)}

    for p in sorted(root.iterdir()):
        if p.is_file():
            preserve(p, Path('root-attempt') / p.name)
    for p in sorted(run_dir.rglob('*')):
        if p.is_file():
            preserve(p, Path('run') / p.relative_to(run_dir))
    preserve(journal_path, Path('retirement-journal.json'))
    preserve(UNIT, Path('installed-apply.service'))
    preserve(CONFIG / 'units/marketdata-checkpoint-retirement.service', Path('canonical-automatic.service'))

    protected = {p: helper.fingerprint(p) == value for p, value in before['protected'].items()}
    existing = {p: sha(Path(p).read_bytes()) == value for p, value in before['registry_files'].items()}
    rows = []
    inventories = []
    for category in ('candidates', 'keep', 'excluded'):
        for candidate in plan[category]:
            entry = candidate['entry'] if category == 'candidates' else candidate
            p = Path(entry['path'])
            row = {'category': category, 'backup_id': entry['backup_id'], 'path': str(p),
                   'present': p.is_dir(), 'manifest_matches': sha((p / 'backup.json').read_bytes()) == entry['manifest_sha256']}
            if category == 'candidates':
                st = p.lstat()
                row['root_matches'] = candidate['root'] == {'dev': st.st_dev, 'ino': st.st_ino, 'mode': st.st_mode}
                item = journal['items'][entry['backup_id']]
                row.update(phase=item['phase'], unlinked_files=item['unlinked_files'], staged_present=Path(item['staged']).exists())
                inv = STATE / 'retention/inventories' / PLAN / (entry['backup_id'] + '.json')
                raw = inv.read_bytes()
                canonical = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                inventories.append({'path': str(inv), 'bytes': len(raw), 'sha256': sha(raw),
                                    'tree_matches': sha(canonical) == candidate['tree_sha256']})
            rows.append(row)
    stage = Path(next(iter(journal['items'].values()))['staged']).parent
    processes = helper.manager()
    processes_match = all(all(processes[n][k] == before['units'][n][k]
                             for k in ('MainPID', 'InvocationID', 'ActiveState')) for n in helper.PROCESS_UNITS)
    grants = {name: {'fingerprint': helper.fingerprint(CONFIG / name),
                     'disabled': all(json.loads((CONFIG / name).read_bytes()).get(k) == v
                                     for k, v in [('enabled', False), ('approved_at', None), ('approval_reference', None)])}
              for name in ('manual.json', 'recurring.json')}
    masks = {str(p): p.is_symlink() and os.readlink(p) == '/dev/null' for p in helper.MASKS}
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'plan_id': PLAN, 'run_id': RUN,
              'root_attempt': ATTEMPT, 'error': run['error'], 'run_state': run['state'],
              'run_exit_code': run['exit_code'], 'deleted_this_run': run['deleted_this_run'],
              'started_at': run['started_at'], 'finished_at': run['finished_at'],
              'journal_status': journal['status'], 'journal_attempts': journal['attempts'],
              'checkpoint_checks': rows, 'inventories': inventories, 'stage_children': sorted(p.name for p in stage.iterdir()),
              'protected_inputs': protected, 'existing_evidence': existing, 'processes': processes,
              'protected_processes_unchanged': processes_match, 'grants': grants, 'masks': masks,
              'plan_unchanged': sha((STATE / 'retention/plans' / (PLAN + '.json')).read_bytes()) == sha((BASE / 't05-formal-preview-2026-10-17/plan.json').read_bytes()),
              'recurring_matches_approved_after': grants['recurring.json']['fingerprint'] == scope_plan['after']['fingerprint'],
              'unit': helper.fingerprint(UNIT), 'operating_mutations': False,
              'validation_scope': 'root identity and manifest hashes; retained full-data validation is inferred from installed control flow and journal creation, not repeated here'}
    result['passed'] = (run['exit_code'] == 1 and 'Errno 18' in run['error'] and journal['status'] == 'interrupted'
                        and all(protected.values()) and all(existing.values()) and processes_match
                        and all(masks.values()) and all(g['disabled'] for g in grants.values())
                        and result['plan_unchanged'] and result['recurring_matches_approved_after']
                        and not result['stage_children'] and all(i['tree_matches'] for i in inventories)
                        and all(r['present'] and r['manifest_matches'] and r.get('root_matches', True)
                                and r.get('phase', 'prepared') == 'prepared' and r.get('unlinked_files', 0) == 0
                                and not r.get('staged_present', False) for r in rows))
    for name, value in [('audit.json', result), ('evidence-manifest.json', manifest)]:
        (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    unit = 'marketdata-checkpoint-retirement-apply@' + PLAN + '.service'
    commands = {'effective-unit.txt': ['/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ReadWritePaths', '-p', 'ProtectSystem', '-p', 'FragmentPath', '-p', 'Result', '-p', 'InvocationID'],
                'manager-journal.jsonl': ['/usr/bin/journalctl', '--no-pager', '-u', unit, '--since', '2026-10-17 16:50:00', '--until', '2026-10-17 17:16:00', '-o', 'json']}
    for name, command in commands.items():
        p = subprocess.run(command, capture_output=True, check=True, timeout=30)
        (HERE / name).write_bytes(p.stdout)
    (HERE / 'host-mountinfo.txt').write_text('\n'.join(l for l in Path('/proc/self/mountinfo').read_text().splitlines() if '/mnt/backup' in l) + '\n')
    print(json.dumps({'audit_passed': result['passed'], 'sources': len(rows), 'inventories': len(inventories),
                      'evidence_copies': sum(x['copy'] is not None for x in manifest.values()),
                      'evidence_index_entries': len(manifest), 'journal_status': journal['status'],
                      'original_sources_preserved': all(r['present'] and r['manifest_matches'] for r in rows),
                      'stage_children': result['stage_children'], 'protected_inputs': len(protected),
                      'existing_evidence': len(existing), 'operating_mutations': False}, indent=2))


if __name__ == '__main__':
    main()
