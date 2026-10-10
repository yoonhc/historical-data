"""Describe a non-editable installation without opening any market-data store."""
import hashlib
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path


def inspect():
    root = Path(sys.prefix).resolve()
    site = root / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    hashes = {}
    for p in sorted(site.rglob('*')):
        rel = p.relative_to(site)
        if not p.is_file() or any(x.endswith('.dist-info') or x == '__pycache__' for x in rel.parts):
            continue
        if p.suffix in {'.pyc', '.pyo'}:
            continue
        hashes[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
    distributions = {d.metadata['Name']: d.version for d in importlib.metadata.distributions(path=[str(site)])}
    imports = {}
    for name in ('daily_schedule', 'daily_review', 'daily_preview', 'daily_probe'):
        module = importlib.import_module('marketdata.' + name)
        p = Path(module.__file__).resolve()
        if site not in p.parents:
            raise RuntimeError('Unexpected import path: ' + str(p))
        imports[name] = {'relative_path': str(p.relative_to(site)),
                         'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for d in importlib.metadata.distributions(path=[str(site)]):
        raw = d.read_text('direct_url.json')
        if raw and json.loads(raw).get('dir_info', {}).get('editable'):
            raise RuntimeError('Editable distribution: ' + d.metadata['Name'])
    return {'python_version': sys.version.split()[0], 'distributions': distributions,
            'site_files_sha256': hashes, 'imports': imports, 'editable': False}


if __name__ == '__main__':
    print(json.dumps(inspect(), sort_keys=True, indent=2))
