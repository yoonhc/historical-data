"""Only extend pre-step inventory; parent clock policy/step protocol is unchanged."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pinned
from systemd_read import PROPERTIES, inactive, parse, verify

TOPOLOGY = '/etc/marketdata/scheduled-system/topology.json'

def host_class(gate, *, topology_path=TOPOLOGY):
    class SystemJobsHost(gate.Host):
        def topology(self):
            gate.trusted(topology_path)
            if gate.rt.digest(topology_path) != self.p['guard_files'].get(topology_path):
                raise gate.Hold('Topology is not pinned by the bootstrap profile')
            topology = json.loads(Path(topology_path).read_text())
            if topology['schema'] != 1 or topology['purpose'] != 'scheduled-manager-only-v1':
                raise gate.Hold('Unknown topology contract')
            return topology

        def audit_quiescent(self):
            # Includes all 16 old jobs/timers being persistently masked, the old
            # read-only waiter, pinned files, and the two system collectors.
            super().audit_quiescent()
            topology = self.topology()
            raw = self.runner(['/usr/bin/systemctl', '--system', 'list-unit-files',
                               'marketdata-*', '--no-legend', '--no-pager'])
            actual = {line.split()[0] for line in raw.splitlines() if line.strip()}
            if actual != set(topology['system_inventory']):
                raise gate.Hold('System unit inventory differs')
            for name, spec in topology['units'].items():
                raw = self.runner(['/usr/bin/systemctl', '--system', 'show', name,
                                   '--no-pager', '--property=' + PROPERTIES])
                props = parse(raw)
                verify(props, spec, quiescent=spec['kind'] == 'work')
            # Previously installed T05 entrypoints may exist, but must not run
            # during initial stepping. Never activate them as part of migration.
            raw = self.runner(['/usr/bin/systemctl', '--system', 'list-units',
                'marketdata-checkpoint-retirement*', '--all', '--plain', '--no-legend', '--no-pager'])
            for line in raw.splitlines():
                if not line.strip():
                    continue
                name = line.split()[0]
                props = parse(self.runner(['/usr/bin/systemctl', '--system', 'show', name,
                                '--property=ActiveState,MainPID,ControlPID']))
                if not inactive(props):
                    raise gate.Hold('T05 work is not quiescent: ' + name)
    return SystemJobsHost

def main():
    # No enroll/RTC/step command interface added. The already reviewed bootstrap
    # implementation alone owns its at-most-once, same-boot step protocol.
    if len(sys.argv) != 4 or sys.argv[1:3] != ['bootstrap', '--profile']:
        raise SystemExit('Only bootstrap --profile PATH is supported')
    gate = pinned.load_gate()
    gate.Host = host_class(gate)
    gate.main()

if __name__ == '__main__':
    main()
