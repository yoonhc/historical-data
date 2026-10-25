"""Exercise installed T05 authorization with a temporary grant read only.

The real settings, policy and scope are used. Only the recurring grant's read
is redirected to a temporary file; no preview, apply, producer or timer runs.
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refresh_scope as r

sys.path[:0] = [str(r.RELEASE), str(r.RELEASE / 'packages/marketdata/src')]
from scripts import clock_retirement as c


def main():
    plan = json.loads((HERE / 'plan.json').read_bytes())
    r.validate_plan(plan)
    result = {'plan_id': plan['plan_id'], 'checked_at': datetime.now(timezone.utc).isoformat(),
              'operating_write': False, 'preview_executed': False,
              'test_substitution': 'recurring grant read redirected to a temporary file only',
              'installed_authorize_source': str(Path(c.__file__).resolve()),
              'installed_authorize_sha256': r.sha(Path(c.__file__).read_bytes())}
    with r.locks():
        before = r.verify_live(plan)
        settings = c.Settings.load(r.CONFIG / 'settings.toml')
        policy = c.retirement.Policy.load(settings.retention_policy)
        profile = c.archive.Profile.load(policy.archive_profile)
        try:
            c.authorize(settings, policy, profile, 'preview')
        except c.legacy.Deferred as exc:
            r.require(exc.reason == 'authorization_scope_changed', 'Unexpected current preview decision')
            result['current_preview_authorization'] = exc.reason
        else:
            raise RuntimeError('Original mismatched grant unexpectedly accepted')
        original_read = Path.read_bytes
        with tempfile.TemporaryDirectory(prefix='t05-disabled-grant-contract-') as tmp:
            grant = Path(tmp) / 'recurring.json'
            grant.write_text(plan['after']['text'])
            with patch.object(Path, 'read_bytes', lambda path: original_read(grant if path == r.TARGET else path)):
                auth = c.authorize(settings, policy, profile, 'preview')
                r.require(auth['enabled'] is False and auth['scope_id'] == json.loads(plan['after']['text'])['scope_id'],
                          'New disabled preview authorization differs')
                result['proposed_preview_authorization'] = 'accepted_disabled'
                try:
                    c.authorize(settings, policy, profile, 'run')
                except c.legacy.Deferred as exc:
                    r.require(exc.reason == 'authorization_pending', 'Unexpected recurring authority')
                    result['proposed_recurring_deletion'] = exc.reason
                else:
                    raise RuntimeError('Disabled grant allowed automatic deletion')
                try:
                    c.authorize(settings, policy, profile, 'apply-plan', '0' * 64)
                except c.legacy.Deferred as exc:
                    r.require(exc.reason == 'authorization_scope_changed', 'Unexpected manual template decision')
                    result['unchanged_manual_template'] = exc.reason
                else:
                    raise RuntimeError('Disabled manual template allowed deletion')
        after = r.verify_live(plan)
        r.require(before == after, 'Host changed during contract verification')
    done = subprocess.run([sys.executable, '-B', '-I', str(HERE / 'refresh_scope.py'), '--action', 'check'],
                          capture_output=True, text=True, timeout=45)
    r.require(done.returncode == 0, 'Standalone read-only preflight failed: ' + done.stderr)
    result['standalone_check'] = json.loads(done.stdout)
    result['passed'] = True
    r.write_new(HERE / 'authorization-validation.json', r.blob(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
