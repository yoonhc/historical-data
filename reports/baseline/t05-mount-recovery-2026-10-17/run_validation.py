"""Run only the changed recovery boundaries against the installed T05 sources."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELEASE = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
SOURCE = HERE.parent / 't05-completion-evidence-2026-10-11/source.tar.gz'
BINARY = ROOT / '.cache/restic/0.19.1/restic'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    with tempfile.TemporaryDirectory(prefix='t05-mount-validation-') as directory:
        trial = Path(directory)
        tests = trial / 'tests'; tests.mkdir()
        with tarfile.open(SOURCE) as archive:
            for member in archive.getmembers():
                if member.isfile() and '/tests/' in '/' + member.name and member.name.endswith('.py'):
                    (tests / Path(member.name).name).write_bytes(archive.extractfile(member).read())
        own = ['test_recovery_contract.py', 'test_installed_recovery.py']
        for name in own:
            shutil.copyfile(HERE / name, tests / name)
        tested_files = {name: sha(tests / name) for name in own}
        tested_files['repair_and_resume.py'] = sha(HERE / 'repair_and_resume.py')
        code = '''import json,sys
from pathlib import Path
release,tests,binary,report=sys.argv[1:]
sys.path[:0]=[release,str(Path(release)/'packages/marketdata/src'),tests]
import pytest,test_restic_archive
test_restic_archive.BINARY=Path(binary)
from scripts import clock_retirement,retire_checkpoints
from marketdata import checkpoint_evidence
for module in (clock_retirement,retire_checkpoints,checkpoint_evidence):
    assert Path(module.__file__).is_relative_to(Path(release)),module.__file__
    print('installed_module='+module.__file__,flush=True)
raise SystemExit(pytest.main(['-c','/dev/null','--rootdir',tests,'--confcutdir',tests,'-p','no:cacheprovider','-v','--tb=short','--junitxml',report,str(Path(tests)/'test_recovery_contract.py'),str(Path(tests)/'test_installed_recovery.py')]))
'''
        start = time.monotonic()
        done = subprocess.run([str(ROOT / '.venv/bin/python'), '-B', '-I', '-c', code,
                               str(RELEASE), str(tests), str(BINARY), str(HERE / 'tests.xml')],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=trial,
                              env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=300)
        (HERE / 'tests.txt').write_text(done.stdout)
        suites = list(ET.parse(HERE / 'tests.xml').getroot())
        counts = {k: sum(int(s.get(k, '0')) for s in suites) for k in ('tests', 'errors', 'failures', 'skipped')}
        result = {'passed': done.returncode == 0 and counts['skipped'] == 0, 'exit_code': done.returncode,
                  'seconds': time.monotonic() - start, **counts,
                  'installed_release': str(RELEASE), 'source_archive_sha256': sha(SOURCE),
                  'restic_binary_sha256': sha(BINARY),
                  'tested_files': tested_files,
                  'operating_mutation': False,
                  'scope': 'actual installed T05, independent tiny restic repository; injected Chrony/manager/ownership; separate real systemd rename report'}
        (HERE / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if done.returncode:
            print(done.stdout[-16000:])
        return done.returncode


if __name__ == '__main__':
    raise SystemExit(main())
