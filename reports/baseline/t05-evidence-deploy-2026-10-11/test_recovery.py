"""Actual dependency-order output regression plus the original 11 helper cases."""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deploy_common as c
import dependency_reader as reader
from test_deployment import DeploymentTests


class DependencyTests(unittest.TestCase):
    def captures(self):
        failed = json.loads((HERE / 'activation-result.json').read_text())
        events = [json.loads(x) for x in (HERE / 'activation-events.jsonl').read_text().splitlines()]
        new = c.properties(events[3]['stdout'])
        previous = failed['before'][c.one(new, 'Id')]
        return previous, new

    def test_actual_failed_activation_order_only(self):
        previous, new = self.captures()
        untouched = copy.deepcopy((previous, new))
        with self.assertRaisesRegex(RuntimeError, 'Service property differs: After'):
            reader.ORIGINAL_VERIFY(new, previous, c.expected_command(c.one(new, 'Id')))
        reader.verify_service(new, previous, c.expected_command(c.one(new, 'Id')))
        self.assertEqual((previous, new), untouched)

    def test_actual_rollback_both_services(self):
        failed = json.loads((HERE / 'activation-result.json').read_text())
        readback = json.loads((HERE / 'failed-activation-readback.json').read_text())
        for name, actual in readback['units'].items():
            reader.verify_service(actual, failed['before'][name])

    def test_missing_dependency_rejected(self):
        previous, new = self.captures()
        for key in ('After', 'Requires'):
            with self.subTest(key=key):
                changed = copy.deepcopy(new)
                changed[key] = [' '.join(x for x in c.one(changed, key).split() if x != 'marketdata-clock-ready.service')]
                with self.assertRaisesRegex(RuntimeError, key):
                    reader.verify_service(changed, previous, c.expected_command(c.one(new, 'Id')))

    def test_added_dependency_rejected(self):
        previous, new = self.captures()
        for key in ('After', 'Requires'):
            changed = copy.deepcopy(new)
            changed[key][0] += ' unapproved.service'
            with self.assertRaisesRegex(RuntimeError, key):
                reader.verify_service(changed, previous, c.expected_command(c.one(new, 'Id')))

    def test_execution_order_is_still_significant(self):
        previous, new = self.captures()
        previous['ExecStartPre'] = ['{ argv[]=guard-one ; ignore_errors=no ; }', '{ argv[]=guard-two ; ignore_errors=no ; }']
        new['ExecStartPre'] = list(reversed(previous['ExecStartPre']))
        with self.assertRaisesRegex(RuntimeError, 'strict start checks'):
            reader.verify_service(new, previous, c.expected_command(c.one(new, 'Id')))


if __name__ == '__main__':
    reader.install_reader()
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite((loader.loadTestsFromTestCase(DependencyTests),
                                loader.loadTestsFromTestCase(DeploymentTests)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    code = ('dependency_reader.py', 'activation_recovery.py', 'test_recovery.py')
    value = {'passed': result.wasSuccessful(), 'tests': result.testsRun, 'new_regressions': 5,
             'reused_deployment_cases': 11, 'failures': len(result.failures), 'errors': len(result.errors),
             'candidate_tests_rerun': False, 'operating_writes': False,
             'tested_sha256': {n: c.sha((HERE / n).read_bytes()) for n in code}}
    c.write_new(HERE / 'recovery-validation.json', c.blob(value))
    raise SystemExit(0 if result.wasSuccessful() else 1)
