"""Native unit syntax/order validation, with only uninstalled executable paths mapped."""
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
manifest = json.loads((HERE / 'manifest.json').read_text())
installed = '/opt/marketdata/releases/' + manifest['release_id']
directory = HERE / 'static-unit-check'
directory.mkdir(exist_ok=True)
files = []
for source in sorted((HERE / 'rendered/units').glob('*')):
    target = directory / source.name
    # This substitution is test-only; final rendered unit bytes are kept separately.
    target.write_text(source.read_text().replace(installed, str(HERE / 'candidate')))
    files.append(str(target))
env = {**os.environ, 'SYSTEMD_UNIT_PATH': str(directory) + ':/etc/systemd/system:/usr/lib/systemd/system'}
result = subprocess.run(['/usr/bin/systemd-analyze', '--man=no', 'verify', *files],
                        capture_output=True, text=True, env=env, timeout=60)
record = {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
          'units': len(files), 'test_only_path_substitution': {installed: str(HERE / 'candidate')},
          'actual_root_execution': False, 'policy_or_dependency_substitution': False}
(HERE / 'unit-verification.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
raise SystemExit(result.returncode)
