"""Deployment-only regressions; operating services/files are never modified."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deploy_common as c
from activate_evidence import remove_own_dropins


class DeploymentTests(unittest.TestCase):
    def original(self):
        before = json.loads((HERE / 'host-before.json').read_text())
        return c.properties(before['user_units'][c.SERVICES[0]])

    def changed(self):
        previous = self.original()
        actual = copy.deepcopy(previous)
        actual['ExecStart'] = ['{ path=/fixed/python ; argv[]=/fixed/python fixed.py ; ignore_errors=no ; pid=0 ; }']
        actual['DropInPaths'] = [c.one(previous, 'DropInPaths') + ' ' + str(c.USER_UNITS / (c.SERVICES[0]+'.d') / c.DROPIN)]
        actual['NeedDaemonReload'] = ['no']
        return previous, actual

    def test_real_units_preserve_all_prechecks(self):
        previous, actual = self.changed()
        self.assertTrue(c.commands(previous, 'ExecStartPre'))
        c.verify_service(actual, previous, '/fixed/python fixed.py')

    def test_repeated_execstartpre_is_not_overwritten(self):
        a = 'ExecStartPre={ argv[]=guard-one ; ignore_errors=no ; }\nExecStartPre={ argv[]=guard-two ; ignore_errors=no ; }\n'
        b = 'ExecStartPre={ argv[]=guard-one ; ignore_errors=no ; } { argv[]=guard-two ; ignore_errors=no ; }\n'
        self.assertEqual(c.commands(c.properties(a), 'ExecStartPre'), ['guard-one','guard-two'])
        self.assertEqual(c.commands(c.properties(a), 'ExecStartPre'), c.commands(c.properties(b), 'ExecStartPre'))

    def test_repeated_scalar_rejected(self):
        with self.assertRaises(RuntimeError):
            c.properties('User=yoonhc\nUser=root\n')

    def test_missing_precheck_rejected(self):
        previous, actual = self.changed()
        actual['ExecStartPre'] = []
        with self.assertRaisesRegex(RuntimeError, 'strict start checks'):
            c.verify_service(actual, previous, '/fixed/python fixed.py')

    def test_unapproved_service_lifetime_rejected(self):
        previous, actual = self.changed()
        actual['TimeoutStopUSec'] = ['1s']
        with self.assertRaisesRegex(RuntimeError, 'TimeoutStopUSec'):
            c.verify_service(actual, previous, '/fixed/python fixed.py')

    def test_unknown_dropin_rejected(self):
        previous, actual = self.changed()
        actual['DropInPaths'][0] += ' /tmp/unknown.conf'
        with self.assertRaisesRegex(RuntimeError, 'drop-in set'):
            c.verify_service(actual, previous, '/fixed/python fixed.py')

    def test_queued_gate_wait_is_not_an_executing_backup(self):
        values = {'ActiveState':['inactive'],'MainPID':['0'],'ControlPID':['0'],'Job':['1234']}
        self.assertTrue(c.idle(values))
        values['ActiveState'] = ['activating']
        self.assertFalse(c.idle(values))
        values['ActiveState'] = ['inactive']; values['ControlPID'] = ['200']
        self.assertFalse(c.idle(values))

    def test_paths_cannot_escape_release(self):
        for value in ('/etc/passwd','../elsewhere','source/../../etc/x'):
            with self.assertRaises(RuntimeError): c.checked_relative(value)

    def test_exclusive_publication_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t)/'dropin.conf'
            path.write_bytes(b'old')
            with self.assertRaises(FileExistsError): c.publish_new(path,b'new')
            self.assertEqual(path.read_bytes(),b'old')
            self.assertFalse(list(Path(t).glob('.t05-pending-*')))

    def test_interrupted_two_file_activation_rolls_back_only_created_file(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); a=root/'a.conf'; b=root/'b.conf'; evidence=root/'evidence.json'
            evidence.write_bytes(b'preserve-registered-evidence'); b.write_bytes(b'concurrent-change')
            c.publish_new(a,b'new-a')
            with self.assertRaises(FileExistsError): c.publish_new(b,b'new-b')
            remove_own_dropins([a],{str(a):c.sha(b'new-a')})
            self.assertFalse(a.exists()); self.assertEqual(b.read_bytes(),b'concurrent-change')
            self.assertEqual(evidence.read_bytes(),b'preserve-registered-evidence')

    def test_rollback_refuses_modified_file_without_removing_other_file(self):
        with tempfile.TemporaryDirectory() as t:
            a=Path(t)/'a.conf'; b=Path(t)/'b.conf'
            a.write_bytes(b'changed'); b.write_bytes(b'expected-b')
            with self.assertRaisesRegex(RuntimeError,'manual review'):
                remove_own_dropins([a,b],{str(a):c.sha(b'expected-a'),str(b):c.sha(b'expected-b')})
            self.assertTrue(a.exists()); self.assertTrue(b.exists())


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(DeploymentTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'scope':'deployment helper only; no operating mutations','tests':result.testsRun,
            'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),
            'candidate_tests_rerun':False}
    (HERE/'deployment-validation.json').write_bytes(c.blob(report))
    raise SystemExit(0 if result.wasSuccessful() else 1)
