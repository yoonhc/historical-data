"""Narrow regressions for the disabled one-file update; only temp paths mutate."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refresh_scope as r


class RefreshTests(unittest.TestCase):
    def setUp(self):
        source = HERE.parent / 't05-readiness-2026-10-17'
        self.before = json.loads((source / 'installed-disabled-recurring.json').read_bytes())
        self.after = json.loads((source / 'current-disabled-proposal.json').read_bytes())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'recurring.json'
        self.target.write_bytes(r.blob(self.before))
        self.original = r.fingerprint(self.target)
        raw = r.blob(self.after)
        self.desired = {'text': raw.decode(), 'fingerprint': {**self.original, 'sha256': r.sha(raw)}}

    def test_exact_hash_refresh_is_the_only_allowed_change(self):
        r.validate_transition(self.before, self.after)

    def test_enabled_grant_rejected_even_with_valid_scope_hash(self):
        other = copy.deepcopy(self.after)
        other['enabled'] = True
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            r.validate_transition(self.before, other)

    def test_other_policy_change_rejected(self):
        other = copy.deepcopy(self.after)
        other['scope']['min_age_hours'] = 1
        other['scope_id'] = r.sha(r.canonical(other['scope']))
        with self.assertRaisesRegex(RuntimeError, 'exceeds'):
            r.validate_transition(self.before, other)

    def test_old_approval_is_not_carried_forward(self):
        other = copy.deepcopy(self.after)
        other['approval_reference'] = 'old approval'
        with self.assertRaisesRegex(RuntimeError, 'unapproved'):
            r.validate_transition(self.before, other)

    def test_invalid_scope_digest_rejected(self):
        other = copy.deepcopy(self.after)
        other['scope_id'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'Scope ID'):
            r.validate_transition(self.before, other)

    def test_one_file_atomic_apply_and_exact_rollback(self):
        sentinel = self.root / 'baseline.json'
        sentinel.write_bytes(b'original baseline and completion bounds')
        r.replace_checked(self.target, self.original, self.desired, lambda: None)
        self.assertEqual(r.fingerprint(self.target), self.desired['fingerprint'])
        r.replace_checked(self.target, self.desired['fingerprint'],
                          {'text': r.blob(self.before).decode(), 'fingerprint': self.original}, lambda: None)
        self.assertEqual(r.fingerprint(self.target), self.original)
        self.assertEqual(sentinel.read_bytes(), b'original baseline and completion bounds')

    def test_error_before_rename_preserves_old_grant(self):
        def stop():
            raise RuntimeError('configuration changed')
        with self.assertRaisesRegex(RuntimeError, 'configuration changed'):
            r.replace_checked(self.target, self.original, self.desired, stop)
        self.assertEqual(r.fingerprint(self.target), self.original)
        self.assertFalse(list(self.root.glob('.t05-disabled-scope-*')))

    def test_unknown_current_grant_is_not_overwritten(self):
        self.target.write_bytes(b'concurrent operator change')
        with self.assertRaisesRegex(RuntimeError, 'Target changed'):
            r.replace_checked(self.target, self.original, self.desired, lambda: None)
        self.assertEqual(self.target.read_bytes(), b'concurrent operator change')

    def test_change_during_recheck_is_not_overwritten(self):
        def change():
            self.target.write_bytes(b'concurrent change during validation')
        with self.assertRaisesRegex(RuntimeError, 'before replacement'):
            r.replace_checked(self.target, self.original, self.desired, change)
        self.assertEqual(self.target.read_bytes(), b'concurrent change during validation')

    def test_target_symlink_rejected(self):
        self.target.unlink()
        self.target.symlink_to(self.root / 'unrelated')
        with self.assertRaisesRegex(RuntimeError, 'Target changed'):
            r.replace_checked(self.target, self.original, self.desired, lambda: None)
        self.assertTrue(self.target.is_symlink())

    def test_permissions_preserved(self):
        r.replace_checked(self.target, self.original, self.desired, lambda: None)
        for name in ('uid', 'gid', 'mode', 'nlink'):
            self.assertEqual(r.fingerprint(self.target)[name], self.original[name])

    def operation_fixture(self):
        plan = {'plan_id': '1' * 64,
                'before': {'text': r.blob(self.before).decode(), 'fingerprint': self.original},
                'after': self.desired}
        units = {unit: {'MainPID': '123', 'InvocationID': 'same', 'ActiveState': 'active'}
                 for unit in r.PROCESS_UNITS}
        def live(_):
            current = r.fingerprint(self.target)
            r.require(current in (self.original, self.desired['fingerprint']), 'Unknown current grant')
            return {'position': 'before' if current == self.original else 'after', 'units': units}
        return plan, live

    def test_apply_repeat_rollback_repeat_preserve_evidence(self):
        plan, live = self.operation_fixture()
        with patch.object(r, 'TARGET', self.target), patch.object(r, 'OPS', self.root / 'audit'), \
                patch.object(r, 'verify_live', live), patch.object(r, 'trusted_directory'), patch.object(r.os, 'geteuid', return_value=0):
            first = r.operate(plan, 'apply')
            repeated = r.operate(plan, 'apply')
            back = r.operate(plan, 'rollback')
            back_again = r.operate(plan, 'rollback')
        self.assertTrue(first['grant_changed'])
        self.assertFalse(repeated['grant_changed'])
        self.assertTrue(back['grant_changed'])
        self.assertFalse(back_again['grant_changed'])
        self.assertEqual(r.fingerprint(self.target), self.original)
        self.assertEqual(len(list((self.root / 'audit' / plan['plan_id']).glob('*.result.json'))), 4)

    def test_resume_after_replace_before_result(self):
        plan, live = self.operation_fixture()
        original_replace = r.replace_checked
        def interrupted(*args):
            original_replace(*args)
            raise RuntimeError('after atomic rename')
        with patch.object(r, 'TARGET', self.target), patch.object(r, 'OPS', self.root / 'audit'), \
                patch.object(r, 'verify_live', live), patch.object(r, 'trusted_directory'), patch.object(r.os, 'geteuid', return_value=0):
            with patch.object(r, 'replace_checked', interrupted):
                with self.assertRaisesRegex(RuntimeError, 'after atomic rename'):
                    r.operate(plan, 'apply')
            result = r.operate(plan, 'apply')
        self.assertTrue(result['passed'])
        self.assertFalse(result['grant_changed'])
        self.assertEqual(r.fingerprint(self.target), self.desired['fingerprint'])

    def test_readonly_check_creates_no_audit(self):
        plan, live = self.operation_fixture()
        with patch.object(r, 'OPS', self.root / 'audit'), patch.object(r, 'verify_live', live):
            result = r.operate(plan, 'check')
        self.assertFalse(result['operating_write'])
        self.assertFalse((self.root / 'audit').exists())
        self.assertEqual(r.fingerprint(self.target), self.original)

    def test_saved_evidence_tampering_blocks_rollback(self):
        plan, live = self.operation_fixture()
        with patch.object(r, 'TARGET', self.target), patch.object(r, 'OPS', self.root / 'audit'), \
                patch.object(r, 'verify_live', live), patch.object(r, 'trusted_directory'), patch.object(r.os, 'geteuid', return_value=0):
            r.operate(plan, 'apply')
            (self.root / 'audit' / plan['plan_id'] / 'before.json').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'Saved evidence changed'):
                r.operate(plan, 'rollback')
        self.assertEqual(r.fingerprint(self.target), self.desired['fingerprint'])

    def manager_output(self):
        rows = []
        for name in (*r.PROCESS_UNITS, *r.RETIREMENT_UNITS,
                     'marketdata-checkpoint-retirement-preview.service'):
            row = {'Id': name, 'LoadState': 'masked' if name in r.RETIREMENT_UNITS else 'loaded',
                   'ActiveState': 'active' if name in r.PROCESS_UNITS else 'inactive',
                   'Job': '', 'InvocationID': 'same'}
            if name.endswith('.service'):
                row.update(MainPID='123' if name in r.PROCESS_UNITS else '0', ControlPID='0')
            rows.append('\n'.join(f'{k}={v}' for k, v in row.items()))
        return '\n\n'.join(rows)

    def test_timer_does_not_require_service_pid_properties(self):
        with patch.object(r, 'command', return_value=self.manager_output()):
            values = r.manager()
        self.assertNotIn('MainPID', values[r.RETIREMENT_UNITS[1]])

    def test_queued_retirement_timer_is_rejected(self):
        output = self.manager_output().replace('Job=\n', 'Job=99\n')
        with patch.object(r, 'command', return_value=output):
            with self.assertRaisesRegex(RuntimeError, 'queued'):
                r.manager()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RefreshTests))
    (HERE / 'validation.json').write_bytes(r.blob({'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'passed': result.wasSuccessful(),
        'scope': 'temporary files; manager/root ownership preflight mocked in transaction tests',
        'operating_mutations': False, 'tool_sha256': r.sha((HERE / 'refresh_scope.py').read_bytes())}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
