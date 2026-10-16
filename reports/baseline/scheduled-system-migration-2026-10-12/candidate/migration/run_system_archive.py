"""Keep the T05 archiver intact; explicitly attribute new runs to system manager."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pinned

def bind(schedule):
    original = schedule.service_context
    adapter = {'path': str(Path(__file__).resolve()),
               'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'parent': str(Path(schedule.__file__).resolve()),
               'purpose': 'system-manager-provenance-only-v1'}

    def context(*args, **kwargs):
        if 'manager' in kwargs:
            raise RuntimeError('Manager is fixed by this entrypoint')
        value = original(*args, manager='system', **kwargs)
        value['manager_adapter'] = adapter
        return value

    schedule.service_context = context
    return original

def main():
    schedule = pinned.load_archive()
    bind(schedule)
    schedule.main()

if __name__ == '__main__':
    main()
