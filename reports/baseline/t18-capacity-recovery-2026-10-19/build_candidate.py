"""Build a two-file candidate from the unchanged installed archiver; no install."""
from pathlib import Path
import difflib
import hashlib
import json

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
BASE = Path('/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11/preserve/scripts/archive_backups.py')
EXPECTED = '69455f8e2e29387acfc2ab416550d8c656ada8f4d1f93dc833731d5adac4c045'


def main():
    raw = BASE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED
    source = raw.decode()
    old_import = 'from scripts.restic_trial import compare_readers, overlap, require, sample_streams'
    assert source.count(old_import) == 1
    updated = source.replace(old_import, 'from scripts.archive_output import retain_verified_manifest\n' + old_import)
    old = '''        raw, _ = self.restic.command(["dump", snapshot["id"], "/backup.json"])
        require(sha256(raw) == entry["manifest_sha256"], "Snapshot manifest differs")
        return attempts[0]'''
    workspace = (ROOT / 'scripts/archive_backups.py').read_text()
    new = workspace[workspace.index('        raw, receipt = self.restic.command(["dump", snapshot["id"]'):]
    new = new[:new.index('        return attempts[0]') + len('        return attempts[0]')]
    assert updated.count(old) == 1
    updated = updated.replace(old, new)
    dest = HERE / 'candidate'
    dest.mkdir(exist_ok=True)
    (dest / 'archive_backups.py').write_text(updated)
    (dest / 'archive_output.py').write_bytes((ROOT / 'scripts/archive_output.py').read_bytes())
    (HERE / 'installed-archiver.patch').write_text(''.join(difflib.unified_diff(
        source.splitlines(True), updated.splitlines(True), fromfile=str(BASE), tofile='candidate/archive_backups.py')))
    identity = {'base': str(BASE), 'base_sha256': EXPECTED, 'candidate_files': {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file()},
        'installed': False, 'timer_resumed': False,
        'preserved': 'Installed T05 clock_bundle and reconciliation hooks; only Archive.match output retention changed.'}
    (HERE / 'candidate-source.json').write_text(json.dumps(identity, indent=2) + '\n')
    print(json.dumps(identity, indent=2))


if __name__ == '__main__':
    main()
