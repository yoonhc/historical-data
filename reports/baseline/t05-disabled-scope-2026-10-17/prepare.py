"""Read the live host and write a review package only in this report directory."""
import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refresh_scope as r

DEPLOY = HERE.parent / 'scheduled-clock-repair-deploy-2026-10-16'


def matches(path, expected):
    actual = r.fingerprint(path)
    kind = expected['kind']
    if kind in ('digest', 'file'):
        r.require(actual['kind'] == 'file' and actual['sha256'] == expected['sha256'],
                  'Final T16 file differs: ' + str(path))
        if 'mode' in expected:
            r.require(actual['mode'] == expected['mode'], 'Final mode differs: ' + str(path))
    elif kind == 'symlink':
        r.require(actual['kind'] == kind and actual['target'] == expected['target'],
                  'Final link differs: ' + str(path))
    else:
        raise RuntimeError('Unknown preserved file kind: ' + kind)
    return actual


def main():
    r.require(os.geteuid() == 1000, 'Prepare as the service account, not root')
    r.require(not (HERE / 'plan.json').exists(), 'Preserve the prepared plan; review changes explicitly')
    with r.locks():
        config_plan = json.loads((DEPLOY / 'configuration-plan.json').read_bytes())
        sources = json.loads((DEPLOY / 'operating-evidence/post-apply-native.json').read_bytes())['source_copies']
        receipt_source = sources['configuration-receipt.json']
        receipt_path = Path(receipt_source['path'])
        r.require(r.sha(receipt_path.read_bytes()) == receipt_source['sha256'], 'T16 root receipt differs')
        receipt = json.loads(receipt_path.read_bytes())
        r.require(receipt['plan_id'] == config_plan['plan_id'] and receipt['direction'] == 'apply'
                  and receipt['step_reopened'] is False, 'T16 configuration is not applied')
        ledger = json.loads(r.LEDGER.read_bytes())
        boot = r.BOOT.read_text().strip()
        profile_path = Path(config_plan['bootstrap_path'])
        profile = json.loads(profile_path.read_bytes())
        r.require(ledger == receipt['ledger_after'] and ledger['boot_id'] == boot
                  and ledger['phase'] == 'closed' and ledger['closed_observation']['ready'] is True,
                  'Closed T16 ledger differs')
        r.require(ledger['profile_sha256'] == r.sha(json.dumps(profile, sort_keys=True).encode()),
                  'Ledger/bootstrap connection differs')
        protected = {}
        for path, change in config_plan['operations'].items():
            protected[path] = matches(path, change['after'])
        for path, expected in config_plan['protected'].items():
            if Path(path) != r.TARGET:
                protected[path] = matches(path, expected)
        for name in ('root-plan.json', 'root-approval.json', 'root-attempt.json', 'configuration-receipt.json'):
            item = sources[name]
            r.require(r.sha(Path(item['path']).read_bytes()) == item['sha256'], 'T16 deployment receipt changed')
            protected[item['path']] = r.fingerprint(item['path'])
        raw = r.TARGET.read_bytes()
        before = json.loads(raw)
        after = r.proposal()
        r.validate_transition(before, after)
        extra = [r.LEDGER, r.MANUAL, *r.MASKS, r.CONFIG / 'settings.toml',
                 r.CONFIG / 'policy.toml', r.CONFIG / 'clock-binding.json', Path(profile['runtime_profile'])]
        policy = tomllib.loads((r.CONFIG / 'policy.toml').read_text())
        extra.extend([Path(policy['archive_profile']), Path(policy['recovery_receipt'])])
        extra.extend(r.CONFIG / 'units' / name for name in after['scope']['system_units'])
        # Existing immutable objects are pinned; later valid additions are allowed.
        objects = Path('/var/lib/marketdata-checkpoint-retirement/evidence/objects')
        known_objects = sorted(objects.glob('*.json'))
        r.require(len(known_objects) >= 13, 'Previously observed completion evidence is missing')
        for path in known_objects:
            r.require(r.sha(path.read_bytes()) == path.stem, 'Evidence object hash differs')
        extra.extend(known_objects)
        for path in extra:
            protected[str(path)] = r.fingerprint(path)
        protected.pop(str(r.TARGET), None)
        target_before = r.fingerprint(r.TARGET)
        text = r.blob(after).decode()
        plan = {'schema': 1, 'purpose': r.PURPOSE, 'target': str(r.TARGET),
                'boot_id': boot, 'created_at': datetime.now(timezone.utc).isoformat(),
                'tool_sha256': r.sha((HERE / 'refresh_scope.py').read_bytes()),
                't16_release_id': config_plan['release_id'], 't16_configuration_plan_id': config_plan['plan_id'],
                'before': {'text': raw.decode(), 'fingerprint': target_before},
                'after': {'text': text, 'fingerprint': {**target_before, 'sha256': r.sha(text.encode())}},
                'protected': dict(sorted(protected.items())),
                'mutations': ['disabled recurring.json only; immutable root audit files'],
                'deletion_authority': False, 'automatic_retirement': 'masked'}
        plan['plan_id'] = r.sha(r.canonical(plan))
        r.validate_plan(plan)
        checked = r.verify_live(plan)
        r.require(checked['position'] == 'before', 'Grant already changed during preparation')
        r.write_new(HERE / 'before.json', raw)
        r.write_new(HERE / 'after.json', text.encode())
        r.write_new(HERE / 'plan.json', r.blob(plan))
        result = {'checked_at': plan['created_at'], 'passed': True, 'plan_id': plan['plan_id'],
                  'operating_write': False, 'installed': False, 'scope_before': before['scope_id'],
                  'scope_after': after['scope_id'], 'changed_scope_fields': ['clock_binding.bootstrap_profile_sha256'],
                  'manual_unchanged_disabled': True, 'existing_evidence_objects': len(known_objects),
                  'protected_files': len(protected), 'units': checked['units'],
                  't16_configuration_operations_matched': len(config_plan['operations']),
                  'formal_preview_run': False, 'deletion_authority': False}
        r.write_new(HERE / 'preparation.json', r.blob(result))
    print(json.dumps({k: v for k, v in result.items() if k != 'units'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
