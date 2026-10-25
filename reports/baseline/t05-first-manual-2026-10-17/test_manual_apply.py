"""Isolated authorization/finalization tests; no systemd or production writes."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('manual_apply', HERE / 'manual_apply.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
spec = importlib.util.spec_from_file_location('reviewed_scope', HERE.parent / 't05-disabled-scope-2026-10-17/refresh_scope.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ManualApplicationTests(unittest.TestCase):
    def setUp(self):
        self.proposal = json.loads((HERE / 'manual-proposal.json').read_bytes())
        self.idle = {'ActiveState':'inactive', 'MainPID':'0', 'ControlPID':'0', 'Job':''}

    def test_enabled_grant_preserves_exact_scope_boot_plan(self):
        original = copy.deepcopy(self.proposal)
        value = app.enabled_grant(self.proposal, '2026-10-17T07:00:00+00:00', 'explicit-user-approval')
        self.assertTrue(value['enabled'])
        self.assertFalse(value['resume_interrupted'])
        for key in set(original) - {'enabled','approved_at','approval_reference'}:
            self.assertEqual(value[key], original[key])
        self.assertEqual(self.proposal, original)

    def test_wrong_plan_or_resume_or_scope_is_rejected(self):
        for key, value in [('plan_id','0'*64), ('resume_interrupted',True), ('action','run'),
                           ('grant_kind','recurring'), ('scope_id','0'*64), ('enabled',True)]:
            with self.subTest(key=key):
                changed = {**self.proposal, key:value}
                with self.assertRaises(RuntimeError):
                    app.validate_grant(changed, self.proposal)

    def test_missing_approval_reference_rejected(self):
        with self.assertRaises(RuntimeError):
            app.enabled_grant(self.proposal, '2026-10-17T07:00:00+00:00', '')

    def test_idle_does_not_confuse_oneshot_or_pending_job(self):
        self.assertTrue(app.idle(self.idle))
        self.assertTrue(app.idle({**self.idle, 'ActiveState':'failed'}))
        for change in [{'ActiveState':'activating'}, {'ActiveState':'deactivating'},
                       {'MainPID':'123'}, {'ControlPID':'123'}, {'Job':'451'}]:
            self.assertFalse(app.idle({**self.idle, **change}))

    def case_files(self, directory):
        path = Path(directory) / 'manual.json'
        old = b'{"enabled":false}\n'
        path.write_bytes(old)
        path.chmod(0o644)
        original = {'text':old.decode(), 'fingerprint':helper.fingerprint(path)}
        new = b'{"enabled":true,"plan_id":"exact"}\n'
        enabled = {'text':new.decode(), 'fingerprint':{**original['fingerprint'], 'sha256':app.sha(new)}}
        helper.MANUAL = path
        helper.replace_checked(path, original['fingerprint'], enabled, lambda: None)
        return path, original, enabled

    def test_idle_success_or_failed_service_restores_original_atomically(self):
        for state in ('inactive','failed'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                path, original, enabled = self.case_files(directory)
                result = app.restore_when_idle(helper, original, enabled,
                                               lambda: {**self.idle,'ActiveState':state})
                self.assertEqual(result, 'original_disabled_restored')
                self.assertEqual(helper.fingerprint(path), original['fingerprint'])
                self.assertEqual(app.restore_when_idle(helper, original, enabled, lambda:self.idle), 'already_disabled')

    def test_running_service_or_changed_authority_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path, original, enabled = self.case_files(directory)
            with self.assertRaises(RuntimeError):
                app.restore_when_idle(helper, original, enabled, lambda: {**self.idle,'MainPID':'123'})
            self.assertEqual(helper.fingerprint(path), enabled['fingerprint'])
            path.write_text('{"different":true}')
            with self.assertRaises(RuntimeError):
                app.restore_when_idle(helper, original, enabled, lambda:self.idle)
            self.assertEqual(path.read_text(), '{"different":true}')

    def test_unit_becomes_active_during_restore_keeps_enabled_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            path, original, enabled = self.case_files(directory)
            rows = iter([self.idle, {**self.idle,'ActiveState':'activating'}])
            with self.assertRaises(RuntimeError):
                app.restore_when_idle(helper, original, enabled, lambda:next(rows))
            self.assertEqual(helper.fingerprint(path), enabled['fingerprint'])
            self.assertEqual([p.name for p in path.parent.iterdir()], ['manual.json'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
