"""Compare captured read-only host evidence; no operating store or service actions."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    before = json.loads((HERE / 'baseline.json').read_text())
    after = json.loads((HERE / 'baseline-after.json').read_text())
    manifest = json.loads((HERE / 'manifest.json').read_text())
    plan = json.loads((HERE / 'plan.json').read_text())
    same = {key: before[key] == after[key] for key in ('files', 'retirement_masks',
        'service_identity', 'user_inventory', 'system_inventory', 'boot_id', 'ledger')}
    parents = {name: sha(Path(name)) == digest for name, digest in manifest['parent_files'].items()}
    workspace = {name: sha(REPO / name) == digest
                 for name, digest in manifest['workspace_identical_sha256'].items()}
    protected = {name: sha(Path(name)) == node['sha256']
                 for name, node in plan['protected'].items() if node['kind'] == 'digest'}
    tests = {}
    for name in ('transition-tests-final.xml', 'parent-bridge-tests.xml',
                 'unchanged-workload-tests.xml', 'reproduction-tests.xml'):
        suites = ET.parse(HERE / name).getroot().findall('testsuite')
        tests[name] = {k: sum(int(s.get(k, 0)) for s in suites)
                       for k in ('tests', 'errors', 'failures', 'skipped')}
        tests[name]['seconds'] = sum(float(s.get('time', 0)) for s in suites)
        tests[name]['sha256'] = sha(HERE / name)
    result = {'release_id': manifest['release_id'], 'plan_id': manifest['plan_id'],
        'captured_before': before['captured_at'], 'captured_after': after['captured_at'],
        'operating_comparison': same, 'protected_external_digests': protected,
        'parent_sources_unchanged': parents, 'workspace_sources_unchanged': workspace,
        'captured_files': len(before['files']), 'system_jobs_after': after['system_jobs'],
        'user_jobs_after': after['user_jobs'], 'tests': tests,
        'source_archive_sha256': sha(HERE / 'source.tar.gz'),
        'reproduction_temporary_directory_absent': json.loads((HERE / 'reproduction.json').read_text())['temporary_directory_absent'],
        'operating_writes': False, 'actual_root_unit_execution': False,
        'catalog_compared': False,
        'note': 'Live acquisition continued; operating catalog hashes are not an unchanged-state assertion.'}
    result['passed'] = (all(same.values()) and all(parents.values()) and all(workspace.values())
                        and all(protected.values()) and all(t['errors'] == t['failures'] == 0 for t in tests.values()))
    (HERE / 'operating-comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k in (
        'passed', 'operating_comparison', 'captured_files', 'operating_writes', 'actual_root_unit_execution')}, indent=2))
    assert result['passed']


if __name__ == '__main__':
    main()
