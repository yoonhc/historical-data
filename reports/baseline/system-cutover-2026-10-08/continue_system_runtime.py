"""Continue only the recorded local-archive fetch failure; preserve the first result."""
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT_RECEIPT = Path('/var/lib/marketdata-cutover-20261008')
INPUTS = json.loads((HERE / 'install-continue-inputs.json').read_text())

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

for path, digest_value in INPUTS['files'].items():
    if sha(path) != digest_value:
        raise SystemExit('Continuation input changed: ' + path)
spec = importlib.util.spec_from_file_location('original_cutover', HERE / 'install_system_runtime.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ROOT, CANDIDATE, RELEASE_ID, RELEASE, OPS, STATE = (base.ROOT, base.CANDIDATE, base.RELEASE_ID, base.RELEASE, base.OPS, base.STATE)
USER_UNITS, GATE, PROFILE, SYSTEM_MASKS, KEEP_MASKED, USER_PREFIX = (base.USER_UNITS, base.GATE, base.PROFILE, base.SYSTEM_MASKS, base.KEEP_MASKED, base.USER_PREFIX)
run, write_new, digest, properties, event = base.run, base.write_new, base.digest, base.properties, base.event


def check_partial(plan, before, with_manager):
    failed = json.loads((OPS / 'result.json').read_text())
    if failed['passed'] or failed['phase'] != 'configuration_and_package' or 'Unable to fetch some archives' not in failed.get('error',''):
        raise RuntimeError('Not the recorded package-fetch failure')
    if STATE.exists() or (OPS / 'result-continuation.json').exists():
        raise RuntimeError('Continuation already attempted or ledger exists; inspect first')
    if sha(OPS / 'installer.py') != sha(HERE / 'install_system_runtime.py'):
        raise RuntimeError('Original executed installer identity changed')
    expected = json.loads((CANDIDATE / 'release.json').read_text())
    for name, value in expected['source_files_sha256'].items():
        if sha(RELEASE / name) != value:
            raise RuntimeError('Installed source differs: '+name)
    receipt = json.loads((RELEASE / 'runtime-manifest.json').read_text())
    old = json.loads((CANDIDATE / 'installation-receipt.json').read_text())
    for key in ('python','distributions','runtime_files_sha256','editable','release_id'):
        if receipt[key] != old[key]: raise RuntimeError('Installed receipt changed: '+key)
    for name, value in receipt['runtime_files_sha256'].items():
        if sha(RELEASE / name) != value: raise RuntimeError('Installed executable changed: '+name)
    boot = json.loads((CANDIDATE / 'boot-profile.json').read_text())
    for name, value in boot['guard_files'].items():
        if sha(name) != value: raise RuntimeError('Installed unit differs: '+name)
    for source, target in [(CANDIDATE/'machine-profile.json','/etc/marketdata/runtime.json'),
                            (CANDIDATE/'boot-profile.json',PROFILE),
                            (HERE/'rtc-utc-confirmation.json','/etc/marketdata/rtc-utc-confirmation.json'),
                            (RELEASE/'integrations/runtime/chrony.conf','/etc/chrony/marketdata.conf')]:
        if sha(source) != sha(target): raise RuntimeError('Installed configuration changed: '+target)
    for name in SYSTEM_MASKS:
        path=Path('/etc/systemd/system')/name
        if not path.is_symlink() or os.readlink(path)!='/dev/null':raise RuntimeError('Install mask absent: '+name)
    if sha(OPS/Path(plan['chrony_package']).name)!=sha(plan['chrony_package']):raise RuntimeError('Chrony package changed')
    if not with_manager:return
    p=properties(run(['timedatectl','show']).stdout)
    if p.get('LocalRTC')!='no' or p.get('NTPSynchronized')!='yes':raise RuntimeError('RTC/NTP changed')
    for path, expected in plan['mounts'].items():
        if run(['findmnt','-T',path,'-n','-o','UUID']).stdout.strip()!=expected:raise RuntimeError('Mount changed')
    if run(['dpkg-query','-W','-f=${Version}','systemd-timesyncd']).stdout.strip()!='255.4-1ubuntu8.16':raise RuntimeError('Package state changed')
    chrony=run(['dpkg-query','-W','-f=${db:Status-Abbrev}','chrony']).stdout.strip()
    if chrony not in ('un','rc'):raise RuntimeError('Chrony already installed')
    values=run(USER_PREFIX+['systemctl','--user','show',*sorted(before['units']),'-p','Id','-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlPID']).stdout
    blocks=[properties(x) for x in values.strip().split('\n\n')]
    if len(blocks)!=20 or any(v.get('LoadState')!='masked' or v.get('ActiveState') not in ('inactive','failed') or v.get('MainPID','0')!='0' or v.get('ControlPID','0')!='0' for v in blocks):raise RuntimeError('Business jobs are not held')
    for name in ['chrony.service','marketdata-clock-bootstrap.service','marketdata-collector@depth.service','marketdata-collector@aggtrade.service']:
        p=properties(run(['systemctl','show',name,'-p','ActiveState','-p','MainPID']).stdout)
        if p.get('ActiveState') not in ('inactive','failed') or p.get('MainPID','0')!='0':raise RuntimeError('Unexpected active system unit: '+name)
    simulation=run(['apt-get','-s','install',OPS/Path(plan['chrony_package']).name]).stdout
    actions=[x for x in simulation.splitlines() if x.startswith(('Inst ','Remv '))]
    if len(actions)!=2 or not any(x.startswith('Remv systemd-timesyncd ') for x in actions) or not any(x.startswith('Inst chrony (4.5-1ubuntu4.2 ') for x in actions):raise RuntimeError('Package scope changed: '+str(actions))


def finish(plan, before):
    base.PHASE = 'configuration_and_package_continuation'
    run(["apt-get", "-y", "-o", "Dpkg::Options::=--force-confold", "install",
         OPS / Path(plan["chrony_package"]).name], timeout=300)
    for name in SYSTEM_MASKS:
        if os.readlink(Path("/etc/systemd/system") / name) != "/dev/null":
            raise RuntimeError("Package altered the temporary mask")
    for name in ("chrony.service", "systemd-timesyncd.service"):
        p = properties(run(["systemctl", "show", name, "-p", "ActiveState", "-p", "MainPID"]).stdout)
        if p.get("ActiveState") not in {"inactive", "failed"} or p.get("MainPID", "0") != "0":
            raise RuntimeError(f"Time daemon unexpectedly running: {name}")
    if run(["dpkg-query", "-W", "-f=${Version}", "chrony"]).stdout.strip() != "4.5-1ubuntu4.2":
        raise RuntimeError("Wrong installed Chrony version")
    for name, expected_digest in plan["chrony_binaries"].items():
        if digest(name) != expected_digest:
            raise RuntimeError(f"Chrony binary changed: {name}")
    run(["/usr/sbin/chronyd", "-p", "-f", "/etc/chrony/marketdata.conf"])
    base.PHASE = "installation_hold"
    print(base.PHASE, flush=True)
    STATE.mkdir(mode=0o755)
    run(GATE + ["enroll", "--profile", PROFILE])
    state = json.loads((STATE / "boot-state.json").read_text())
    if state["phase"] != "installation_hold" or state["boot_id"] != plan["boot_id"] or state["step_attempts"] != 0:
        raise RuntimeError("Installation boot was not held")
    run(GATE + ["rtc-check", "--profile", PROFILE])
    # Restore exactly the 8 job/timer pairs; old collectors and retirement stay masked.
    for name, meta in before["units"].items():
        if name in KEEP_MASKED:
            continue
        live = USER_UNITS / name
        if not live.is_symlink() or os.readlink(live) != "/dev/null":
            raise RuntimeError(f"Mask changed during install: {name}")
        tmp = USER_UNITS / (name + ".cutover-new")
        if tmp.exists() or tmp.is_symlink():
            raise RuntimeError(f"Unexpected restore temporary path: {tmp}")
        if meta.get("symlink"):
            os.symlink(meta["symlink"], tmp)
            os.lchown(tmp, meta["uid"], meta["gid"])
        else:
            write_new(tmp, (HERE / "held-user-units" / name).read_bytes(), meta["mode"])
            os.chown(tmp, meta["uid"], meta["gid"])
        os.replace(tmp, live)
        event("user_unit_restored", name=name, sha256=digest(live))
    run(USER_PREFIX + ["systemctl", "--user", "daemon-reload"])
    denied = run(USER_PREFIX + GATE + ["check", "--profile", PROFILE], allowed=(75,))
    if "This boot has not closed initial clock preparation" not in denied.stderr:
        raise RuntimeError("Expected installation hold, not an unrelated gate error")
    run(["systemctl", "unmask", *SYSTEM_MASKS])
    run(["systemctl", "daemon-reload"])
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
    plan, manifest, before=base.validate()
    check_partial(plan,before,False)
    if sys.argv[1:]==['--validate']:
        print(json.dumps({'valid':True,'resumes':'configuration_and_package','reuses_installed_release':True,'reboot':False}))
        return
    if os.geteuid()!=0:raise SystemExit('Administrator authentication required')
    os.umask(0o022)
    result={'release_id':RELEASE_ID,'boot_id':plan['boot_id'],'passed':False,'first_result_preserved':'result.json'}
    try:
        check_partial(plan,before,True)
        write_new(OPS/'continuation.py',Path(__file__).read_bytes())
        write_new(OPS/'continuation-inputs.json',(HERE/'install-continue-inputs.json').read_bytes())
        base.event('continuation_started',reason='Allow APT local file acquisition; exact package scope rechecked')
        finish(plan,before)
        result.update(passed=True,next='Review actual installed state before approved final Linux reboot')
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
        base.event('continuation_stopped',error=result['error'])
    result.update(phase=base.PHASE,finished_at=datetime.now().astimezone().isoformat(),reboot_performed=False,automatic_checkpoint_retirement='held')
    write_new(OPS/'result-continuation.json',(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
