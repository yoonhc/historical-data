"""Build a reviewable plan. This writes reports only; NEVER authorizes operation."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_common as c
sys.path.insert(0, str(c.BASE / 'candidate/migration'))
import systemd_read as reader

def build(preflight=None):
    inner = json.loads((c.HERE / 'configuration-plan.json').read_bytes())
    baseline = json.loads((c.BASE / 'baseline.json').read_bytes())
    tools = ['handoff.py', 'handoff_common.py', 'handoff_inputs.py', 'system_preflight.py', 'trial_worker.py',
             'build_host_plan.py', 'correct_entrypoints.py', 'configuration-plan.json']
    seal = {'files': {name: c.sha((c.HERE / name).read_bytes()) for name in tools},
            'base_files': {name: c.sha((c.BASE / name).read_bytes()) for name in
                ('manifest.json', 'environment.json', 'plan.json', 'baseline.json', 'source.tar.gz')}}
    seal['tool_id'] = 'scheduled-handoff-20261012-' + c.sha(c.encode(seal))[:16]
    (c.HERE / 'tool-seal.json').write_bytes(c.encode(seal))
    ownership = {}
    for path, op in inner['operations'].items():
        ownership[path] = {}
        for side in ('before', 'after'):
            node = op[side]
            if node['kind'] == 'absent':
                value = {'kind': 'absent'}
            else:
                user = path.startswith(c.USER_ROOT)
                value = {'kind': node['kind'], 'uid': 1000 if user else 0,
                         'gid': 1000 if user else 0, 'mode': node.get('mode', 0o777)}
            ownership[path][side] = value
    effective = {}
    for name, observed in baseline['effective_user_units'].items():
        p = reader.parse(observed['show'])
        fields = ['LoadState', 'FragmentPath', 'DropInPaths', 'WorkingDirectory', 'Restart',
            'RestartUSec', 'KillMode', 'KillSignal', 'TimeoutStartUSec', 'TimeoutStopUSec', 'RuntimeMaxUSec',
            'Unit', 'Persistent', 'AccuracyUSec', 'RandomizedDelayUSec']
        effective[name] = {key: p[key] for key in fields if key in p}
        effective[name].update({key: reader.commands(p.get(key, '')) for key in reader.COMMANDS})
    old_profile = json.loads(inner['operations']['/etc/marketdata/clock-bootstrap.json']['before']['text'])
    extra = {p: h for p, h in old_profile['guard_files'].items() if p not in inner['operations']}
    for name, observed in baseline['effective_user_units'].items():
        path = c.USER_ROOT + name
        before = inner['operations'][path]['before']
        if before['kind'] == 'symlink':
            extra[before['target']] = c.sha(observed['base'].encode())
        for p, raw in observed['dropins'].items():
            extra[p] = c.sha(raw.encode())
    plan = {'purpose': 'scheduled-manager-host-handoff-v1', 'boot_id': inner['boot_id'],
        'tool_id': seal['tool_id'], 'inner': inner, 'policy_changes': [], 'preflight': None,
        'ownership': ownership, 'extra_protected': extra, 'effective_user_before': effective,
        'inventory_before': {'user': baseline['user_inventory'], 'system': baseline['system_inventory']},
        'enable_links': {c.SYSTEM_ROOT + 'timers.target.wants/' + name: {
            'before': {'kind': 'absent'}, 'after': {'kind': 'symlink', 'target': c.SYSTEM_ROOT + name}}
             for name in c.TIMERS},
        'scope': ['sealed /opt release', '46 configuration operations', '8 system timer enable links',
                  'source persistent masks', 'normal drain; cancel recorded no-PID requests',
                  'both manager reloads', 'target timers and cancelled-request reevaluation'],
        'excluded': ['collector/Chrony restart', 'clock/RTC control', 'deletion', 'retirement enable',
                     'T05 registration', 'task policy change', 'data/catalog rollback'],
        'approval_required': True}
    if preflight:
        receipt = json.loads(preflight.read_bytes())
        c.require(receipt['passed'] and receipt['tool_id'] == seal['tool_id'] and receipt['boot_id'] == inner['boot_id'], 'Wrong/failed preflight')
        for path, value in receipt['ownership_before'].items():
            if path in ownership:
                ownership[path]['before'] = value
        plan['preflight'] = {'report': str(preflight.resolve()), 'sha256': c.sha(preflight.read_bytes())}
    plan['plan_id'] = c.sha(c.encode(plan))
    output = c.HERE / ('host-plan.json' if not preflight else 'host-plan-preflight-passed.json')
    output.write_bytes(c.encode(plan))
    approval = {'purpose': plan['purpose'], 'plan_id': plan['plan_id'], 'enabled': False,
                'approval_reference': '', 'directions': ['apply', 'rollback']}
    output.with_suffix('.approval.template.json').write_bytes(c.encode(approval))
    print(json.dumps({'tool_id': plan['tool_id'], 'plan_id': plan['plan_id'],
                      'preflight_passed': bool(preflight), 'operating_approval': False}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', type=Path)
    args = parser.parse_args()
    build(args.preflight)
