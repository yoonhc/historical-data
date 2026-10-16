"""Preserve source release, fix four invalid -m entrypoints in a NEW plan."""
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.with_name('scheduled-system-migration-2026-10-12')
sys.path.insert(0, str(BASE / 'candidate/migration'))
import transaction as tx

def node(value):
    raw = value if isinstance(value, str) else tx.encode(value).decode()
    return {'kind': 'file', 'mode': 0o644, 'text': raw, 'sha256': tx.sha(raw.encode())}

def build():
    original = json.loads((BASE / 'plan.json').read_bytes())
    plan = copy.deepcopy(original)
    for job in ('oi', 'oi-probe', 'oi-quality', 'pack'):
        name = 'marketdata-' + job + '.service'
        key = '/etc/systemd/system/' + name
        old = plan['operations'][key]['after']['text']
        new = old.replace(' -m marketdata ', ' -m marketdata.cli ')
        assert new != old and new.replace(' -m marketdata.cli ', ' -m marketdata ') == old
        plan['operations'][key]['after'] = node(new)
        plan['topology']['units'][name]['ExecStart'] = [s.replace(' -m marketdata ', ' -m marketdata.cli ')
            for s in plan['topology']['units'][name]['ExecStart']]
    topology = '/etc/marketdata/scheduled-system/topology.json'
    plan['operations'][topology]['after'] = node(plan['topology'])
    profile = json.loads(plan['operations'][tx.PROFILE]['after']['text'])
    for key in profile['guard_files']:
        if key in plan['operations']:
            profile['guard_files'][key] = plan['operations'][key]['after']['sha256']
    plan['operations'][tx.PROFILE]['after'] = node(profile)
    binding = '/etc/marketdata/checkpoint-clock-retirement/clock-binding.json'
    plan['operations'][binding]['after'] = node({'bootstrap_profile': tx.PROFILE,
        'bootstrap_profile_sha256': plan['operations'][tx.PROFILE]['after']['sha256']})
    plan['plan_id'] = tx.sha(tx.encode({k: v for k, v in plan.items() if k != 'plan_id'}))
    tx.validate(plan, {'purpose': tx.PURPOSE, 'plan_id': plan['plan_id'], 'enabled': True,
        'directions': ['apply', 'rollback'], 'approval_reference': 'validation-only-not-operating-approval'})
    (HERE / 'configuration-plan.json').write_bytes(tx.encode(plan))
    out = HERE / 'rendered'
    out.mkdir(exist_ok=True)
    for key, value in plan['operations'].items():
        if key.startswith('/etc/systemd/system/'):
            (out / Path(key).name).write_text(value['after']['text'])
    changes = [k for k in plan['operations'] if plan['operations'][k] != original['operations'][k]]
    (HERE / 'entrypoint-correction.json').write_bytes(tx.encode({
        'source_release_unchanged': plan['release_id'], 'original_plan_id': original['plan_id'],
        'new_plan_id': plan['plan_id'], 'changed_configuration_nodes': changes,
        'reason': 'No marketdata.__main__; existing CLI module is marketdata.cli',
        'business_source_changes': [], 'operating_changes': False}))
    print(json.dumps({'plan_id': plan['plan_id'], 'configuration_nodes_changed': len(changes)}))

if __name__ == '__main__':
    build()
