"""Read-only check of the fixed install and its registered graph; run as yoonhc."""
import argparse
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('release', type=Path)
p.add_argument('settings', type=Path)
p.add_argument('--source-entrypoint', action='store_true')
p.add_argument('--registry', action='store_true')
args = p.parse_args()
assert os.geteuid() == 1000 and sys.flags.isolated
assert not os.access(args.release, os.W_OK)
if args.source_entrypoint or args.registry:
    sys.path[:0] = [str(args.release), str(args.release / 'packages/marketdata/src')]
names = ('marketdata.backup', 'marketdata.checkpoint_clock', 'marketdata.checkpoint_evidence')
imports = {}
for name in names:
    module = importlib.import_module(name)
    path = Path(module.__file__).resolve()
    expected = args.release / ('packages/marketdata/src' if args.source_entrypoint or args.registry else '.venv/lib/python3.12/site-packages') / (name.replace('.', '/') + '.py')
    assert path == expected
    imports[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
result = {'uid': os.geteuid(), 'isolated': True, 'release_writable': False, 'imports': imports}
if args.registry:
    from scripts import checkpoint_evidence as evidence
    from scripts.clock_retirement import Settings
    settings = Settings.load(args.settings)
    profile = evidence.profile(settings)
    registry = evidence.registry(settings, profile)
    with registry.lock():
        graph = registry.graph()
        assert len(graph.baselines) == 1
        baseline_id, baseline = next(iter(graph.baselines.items()))
        result['registry'] = {'scope': graph.scope, 'baseline_id': baseline_id,
            'entries': baseline['entries'], 'objects': {k:v['kind'] for k,v in graph.objects.items()},
            'proofs': {ident['backup_id']: graph.proof(ident) for ident in baseline['entries']}}
print(json.dumps(result))
