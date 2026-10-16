"""Use existing reviewed releases; do not silently substitute workspace imports."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def manifest():
    return json.loads((HERE / 'parents.json').read_text())

def check_parents():
    data = manifest()
    for path, expected in data['files'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Parent release changed: ' + path)
    return data

def load_gate():
    data = check_parents()
    spec = importlib.util.spec_from_file_location('scheduled_parent_gate', data['gate'])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def load_archive():
    data = check_parents()
    root = Path(data['t05'])
    sys.path[:0] = [str(root), str(root / 'packages/marketdata/src')]
    from scripts import restic_schedule
    if Path(restic_schedule.__file__).resolve() != root / 'scripts/restic_schedule.py':
        raise RuntimeError('Unexpected archive import')
    return restic_schedule

