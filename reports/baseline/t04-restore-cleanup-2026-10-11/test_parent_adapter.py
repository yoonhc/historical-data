"""New parent inventory adapter tests; temporary directories only."""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parent_cleanup as s


class ParentAdapterTests(unittest.TestCase):
    def fixture(self, tmp):
        lib = s.library()
        parent = tmp / 'trial'
        rows = []
        for name in ('extracted', 'store'):
            root = parent / name
            (root / 'data' / 'empty').mkdir(parents=True)
            (root / 'data' / 'content').write_text(name)
            inventory = tmp / (name + '.gz')
            with gzip.open(inventory, 'wt') as f:
                for row in lib.observed(root):
                    f.write(json.dumps(row) + '\n')
            rows.append({'path': str(root), 'inventory': str(inventory)})
        return lib, parent, rows

    def test_complete_parent_deletion_preserves_permanent_parent_and_sibling(self):
        with tempfile.TemporaryDirectory() as value:
            tmp = Path(value)
            lib, parent, rows = self.fixture(tmp)
            sibling = tmp / 'keep'
            sibling.write_text('keep')
            combined = tmp / 'whole.gz'
            root = s.combined_inventory(lib, parent, rows, combined)
            self.assertEqual(lib.check_tree(parent, combined), 9)
            journal = lib.Journal(tmp / 'journal.jsonl')
            try:
                lib.delete_tree(parent, combined, root, journal)
                self.assertEqual((journal.files, journal.directories), (2, 7))
            finally:
                journal.close()
            self.assertFalse(parent.exists())
            self.assertEqual(sibling.read_text(), 'keep')

    def test_unexpected_parent_child_blocks_plan(self):
        with tempfile.TemporaryDirectory() as value:
            tmp = Path(value)
            lib, parent, rows = self.fixture(tmp)
            (parent / 'unknown').write_text('keep')
            with self.assertRaisesRegex(RuntimeError, 'Unexpected parent contents'):
                s.combined_inventory(lib, parent, rows, tmp / 'whole.gz')
            self.assertTrue((parent / 'unknown').exists())

    def test_changed_subtree_after_plan_rejected(self):
        with tempfile.TemporaryDirectory() as value:
            tmp = Path(value)
            lib, parent, rows = self.fixture(tmp)
            combined = tmp / 'whole.gz'
            s.combined_inventory(lib, parent, rows, combined)
            (parent / 'store' / 'new').write_text('keep')
            with self.assertRaisesRegex(RuntimeError, 'Inventory changed'):
                lib.check_tree(parent, combined)
            self.assertTrue((parent / 'extracted' / 'data' / 'content').exists())


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ParentAdapterTests))
    s.review.save(HERE / 'parent-adapter-tests.json', {
        'passed': result.wasSuccessful(), 'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'scope': 'New parent inventory adapter only; existing 11 deletion-core tests reused, not rerun.',
        'sha256': {name: s.review.digest(HERE / name) for name in ('parent_cleanup.py', 'test_parent_adapter.py')},
        'library_sha256': s.CORE_HASH, 'operating_deletion': False})
    raise SystemExit(0 if result.wasSuccessful() else 1)
