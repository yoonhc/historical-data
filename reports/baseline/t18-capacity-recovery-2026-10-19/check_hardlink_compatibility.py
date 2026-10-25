"""Disposable compatibility check; never changes operating report files."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
RELEASE = Path('/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11')


def main():
    path = RELEASE / 'policy/request_contract.py'
    spec = importlib.util.spec_from_file_location('actual_request_contract', path)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    result = {'python': sys.version, 'contract': str(path),
              'contract_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'operating_files_changed': False, 'cases': []}
    with tempfile.TemporaryDirectory(prefix='t18-hardlink-') as directory:
        root = Path(directory)
        for mode in (0o600, 0o664):
            a, b = root / f'{mode}-a.stdout', root / f'{mode}-b.stdout'
            body = b'{"immutable_manifest":"same bytes"}\n' * 300
            for p in (a, b):
                p.write_bytes(body)
                p.chmod(mode)
            run_id = f'case-{mode}'
            report = root / f'{run_id}.json'
            record = {'run_id': run_id, 'ok': True, 'archive': {'ok': True},
                      'stdout_paths': [str(a), str(b)]}
            report.write_bytes(contract.encode(record))
            sealed = contract.seal_result({'job': 'restic-archive', 'result': record,
                                          'files': [str(report), str(a), str(b)], 'records': []})
            before = contract.classify('restic-archive', 0, sealed, {}, [])
            old_inode = b.stat().st_ino
            temporary = b.with_suffix('.pending-link')
            os.link(a, temporary)
            # A reader of b still sees its original bytes before atomic replace.
            assert b.stat().st_ino == old_inode and b.read_bytes() == body
            os.replace(temporary, b)
            assert a.stat().st_ino == b.stat().st_ino != old_inode
            assert a.stat().st_nlink == b.stat().st_nlink == 2
            assert (a.stat().st_mode & 0o777) == (b.stat().st_mode & 0o777) == mode
            after = contract.classify('restic-archive', 0, sealed, {}, [])
            assert before == after and after['state'] == 'complete'
            for p in (a, b):
                try:
                    with p.open('xb'):
                        raise AssertionError('An existing stdout was opened for overwrite')
                except FileExistsError:
                    pass
            contract.verify_result(sealed)
            result['cases'].append({'mode': oct(mode), 'path_and_bytes_preserved': True,
                                    'existing_request_recovers': True,
                                    'receipt_bytes_unchanged': True,
                                    'current_exclusive_writer_refuses_existing_path': True})
    result['passed'] = True
    (HERE / 'hardlink-compatibility.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
