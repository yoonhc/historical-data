"""Finish the recorded alias-order stop; no package reinstall or ledger enrollment."""
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
INPUTS=json.loads((HERE/'install-finish-inputs.json').read_text())
for name,expected in INPUTS['files'].items():
    if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=expected:raise SystemExit('Input changed: '+name)
spec=importlib.util.spec_from_file_location('original_cutover',HERE/'install_system_runtime.py')
base=importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ROOT, CANDIDATE, RELEASE_ID, RELEASE, OPS, STATE=(base.ROOT,base.CANDIDATE,base.RELEASE_ID,base.RELEASE,base.OPS,base.STATE)
USER_UNITS, GATE, PROFILE, KEEP_MASKED, USER_PREFIX=(base.USER_UNITS,base.GATE,base.PROFILE,base.KEEP_MASKED,base.USER_PREFIX)
run, write_new, digest, properties, event=base.run,base.write_new,base.digest,base.properties,base.event

def validate():
    failed=json.loads((OPS/'result-continuation.json').read_text())
    if failed['passed'] or failed['phase']!='installation_hold' or 'Unit chronyd.service not found' not in failed['error']:raise RuntimeError('Not the recorded alias stop')
    if (OPS/'result-finish.json').exists():raise RuntimeError('Finish was already attempted; inspect first')
    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    state=json.loads((STATE/'boot-state.json').read_text())
    if state['boot_id']!=boot_id or boot_id!=INPUTS['boot_id'] or state['phase']!='installation_hold' or state['step_attempts']!=0:raise RuntimeError('Ledger/boot changed')
    if Path('/etc/adjtime').read_text().strip().splitlines()[-1]!='UTC':raise RuntimeError('RTC changed')
    m=json.loads((CANDIDATE/'release.json').read_text())
    for name,h in m['source_files_sha256'].items():
        if digest(RELEASE/name)!=h:raise RuntimeError('Installed source changed: '+name)
    installed=json.loads((RELEASE/'runtime-manifest.json').read_text())
    expected=json.loads((CANDIDATE/'installation-receipt.json').read_text())
    for key in ['python','distributions','runtime_files_sha256','editable','release_id']:
        if installed[key]!=expected[key]:raise RuntimeError('Receipt differs: '+key)
    for name,h in installed['runtime_files_sha256'].items():
        if digest(RELEASE/name)!=h:raise RuntimeError('Installed runtime changed: '+name)
    p=json.loads((CANDIDATE/'boot-profile.json').read_text())
    for name,h in p['guard_files'].items():
        if digest(name)!=h:raise RuntimeError('Guard changed: '+name)
    for source,target in [(CANDIDATE/'boot-profile.json',PROFILE),(CANDIDATE/'machine-profile.json','/etc/marketdata/runtime.json'),(HERE/'rtc-utc-confirmation.json','/etc/marketdata/rtc-utc-confirmation.json'),(RELEASE/'integrations/runtime/chrony.conf','/etc/chrony/marketdata.conf')]:
        if digest(source)!=digest(target):raise RuntimeError('Installed configuration changed: '+str(target))
    before=json.loads((HERE/'before.json').read_text())
    for name,meta in before['units'].items():
        live=USER_UNITS/name
        if name in KEEP_MASKED:
            if not live.is_symlink() or os.readlink(live)!='/dev/null':raise RuntimeError('Exclusion mask changed: '+name)
        elif digest(live)!=meta['sha256']:raise RuntimeError('Restored unit differs: '+name)
    for name,meta in before['config_hashes'].items():
        if name!='/etc/adjtime' and digest(name)!=meta['sha256']:raise RuntimeError('Original configuration changed: '+name)
    return before

def finish(before):
    base.PHASE='alias_registration_then_verify'
    # Register the distro Alias before verifying chrony-wait's dependency.
    run(['systemctl','enable','chrony.service'])
    run(['systemctl','daemon-reload'])
    run(["systemd-analyze", "verify", "chrony.service", "chrony-wait.service",
         "marketdata-clock-bootstrap.service", "marketdata-collector@depth.service",
         "marketdata-collector@aggtrade.service"])
    run(USER_PREFIX + ["systemd-analyze", "--user", "verify", "marketdata-clock-ready.service",
                     *sorted(n for n in before["units"] if n.endswith(".service") and n not in KEEP_MASKED)])
    # Start only the reviewed steady daemon. No bootstrap/makestep in this boot.
    run(["systemctl", "enable", "chrony.service"])
    run(["systemctl", "start", "chrony.service"])
    base.PHASE = "effective_access_checks"
    print(base.PHASE, flush=True)
    for name in ("root-access-probe.py", "consumer-access-probe.py"):
        write_new(OPS / name, (HERE / name).read_bytes())
    common = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect",
              "-p", "NoNewPrivileges=yes", "-p", "ProtectClock=yes",
              "-p", "ProtectSystem=strict", "-p", "ProtectHome=read-only", "-p", "PrivateTmp=yes",
              "-p", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK"]
    run(common + ["--unit=md-cutover-root-access-20261008",
         "-p", "CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SETUID CAP_SETGID",
         "-p", "ReadWritePaths=/run/chrony", "-p", "StateDirectory=marketdata-clock",
         "-p", "StateDirectoryMode=0755", RELEASE / ".venv/bin/python", "-B", "-I",
         OPS / "root-access-probe.py"])
    evidence = ROOT / "state/runtime-clock"
    if not evidence.exists():
        evidence.mkdir(mode=0o700)
        os.chown(evidence, 1000, 1000)
    run(common + ["--unit=md-cutover-consumer-access-20261008", "-p", "User=yoonhc",
         "-p", "CapabilityBoundingSet=", "-p", f"WorkingDirectory={RELEASE}",
         "-p", f"ReadWritePaths={ROOT} {evidence}", RELEASE / ".venv/bin/python", "-B", "-I",
         OPS / "consumer-access-probe.py"])
    base.PHASE = "arm_next_boot"
    print(base.PHASE, flush=True)
    run(["systemctl", "enable", "marketdata-clock-bootstrap.service", "marketdata-collector@depth.service",
         "marketdata-collector@aggtrade.service", "chrony-wait.service"])
    timers = sorted(p.name for p in (HERE / "saved-user-units/timers.target.wants").iterdir()
                    if p.name in before["units"] and p.name not in KEEP_MASKED)
    if len(timers) != 8:
        raise RuntimeError(f"Original enabled timers differ: {timers}")
    run(USER_PREFIX + ["systemctl", "--user", "enable", *timers])
    run(USER_PREFIX + ["systemctl", "--user", "start", *timers])
    run(USER_PREFIX + ["systemctl", "--user", "list-jobs", "--no-pager"])
    run(USER_PREFIX + ["systemctl", "--user", "show", *sorted(before["units"]),
        "marketdata-clock-ready.service", "-p", "Id", "-p", "LoadState", "-p", "ActiveState",
        "-p", "MainPID", "-p", "ControlPID", "-p", "ExecStartPre", "-p", "Requires", "-p", "After"])
    # The same protected audit also recognizes a read-only waiter as safe.
    run(common + ["--unit=md-cutover-root-final-20261008",
         "-p", "CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SETUID CAP_SETGID",
         "-p", "ReadWritePaths=/run/chrony", "-p", "StateDirectory=marketdata-clock",
         "-p", "StateDirectoryMode=0755", RELEASE / ".venv/bin/python", "-B", "-I",
         OPS / "root-access-probe.py"])
    for name in KEEP_MASKED:
        if not (USER_UNITS / name).is_symlink() or os.readlink(USER_UNITS / name) != "/dev/null":
            raise RuntimeError(f"Persistent exclusion mask lost: {name}")
    for name, meta in before["config_hashes"].items():
        if name != "/etc/adjtime" and digest(name) != meta["sha256"]:
            raise RuntimeError(f"Existing configuration changed: {name}")
    state = json.loads((STATE / "boot-state.json").read_text())
    if state["phase"] != "installation_hold" or state["step_attempts"] != 0:
        raise RuntimeError("Unexpected bootstrap execution during installation")
    run(["systemctl", "show", "chrony.service", "chrony-wait.service", "marketdata-clock-bootstrap.service",
         "marketdata-collector@depth.service", "marketdata-collector@aggtrade.service",
         "-p", "Id", "-p", "ActiveState", "-p", "UnitFileState", "-p", "MainPID", "-p", "ExecStart"])
    run(["/usr/bin/chronyc", "-n", "-c", "-h", "127.0.0.1", "tracking"])
    base.PHASE = "installed_pending_review_and_final_reboot"


def main():
    if sys.argv[1:] not in (['--validate'],['--run']):raise SystemExit('Use --validate or --run')
    before=validate()
    if sys.argv[1:]==['--validate']:
        print(json.dumps({'valid':True,'resumes':'alias registration and remaining access/activation checks','package_reinstall':False,'ledger_enrollment':False,'reboot':False}));return
    if os.geteuid()!=0:raise SystemExit('Administrator authentication required')
    os.umask(0o022)
    result={'release_id':RELEASE_ID,'boot_id':INPUTS['boot_id'],'passed':False,'previous_results_preserved':True}
    try:
        write_new(OPS/'finish.py',Path(__file__).read_bytes())
        write_new(OPS/'finish-inputs.json',(HERE/'install-finish-inputs.json').read_bytes())
        base.event('finish_started',reason='Register package Alias before dependency verification')
        for name in ['chrony.service','marketdata-clock-bootstrap.service','marketdata-collector@depth.service','marketdata-collector@aggtrade.service']:
            v=properties(run(['systemctl','show',name,'-p','ActiveState','-p','MainPID']).stdout)
            if v.get('ActiveState') not in ('inactive','failed') or v.get('MainPID','0')!='0':raise RuntimeError('Unexpected running system unit: '+name)
        run(GATE+['rtc-check','--profile',PROFILE])
        run(USER_PREFIX+GATE+['check','--profile',PROFILE],allowed=(75,))
        finish(before)
        result.update(passed=True,next='Review actual installed state, then final Linux reboot')
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
        base.event('finish_stopped',error=result['error'])
    result.update(phase=base.PHASE,finished_at=datetime.now().astimezone().isoformat(),reboot_performed=False,automatic_checkpoint_retirement='held')
    write_new(OPS/'result-finish.json',(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
