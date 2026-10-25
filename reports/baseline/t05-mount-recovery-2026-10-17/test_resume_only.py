import copy
import importlib.util
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


def module(name, path):
    s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


n=module('tested_no_reload_resume',HERE/'resume_only.py')
m=module('tested_frozen_mount',HERE/'repair_and_resume.py')
h=module('tested_atomic_helper',HERE.parent/'t05-disabled-scope-2026-10-17/refresh_scope.py')


@pytest.mark.parametrize('realtime,monotonic,allowed', [
    (3600,0,True),(0,3600,True),(3600,5,False),(5,0,False),(60,0,False),(-1,0,False),(0,2**64-1,True)])
def test_future_timer_admission(monkeypatch,realtime,monotonic,allowed):
    monkeypatch.setattr(n.time,'time',lambda:10000)
    monkeypatch.setattr(n.time,'monotonic',lambda:20000)
    def command(argv):
        delay=realtime if argv[-1]=='NextElapseUSecRealtime' else monotonic
        current=10000 if argv[-1]=='NextElapseUSecRealtime' else 20000
        usec=delay if delay in (0,2**64-1) else int((current+delay)*1e6)
        return json.dumps({'type':'t','data':usec})
    fake=SimpleNamespace(PRESERVATION_UNITS=('test-backup.service',),command=command,require=m.require)
    if allowed:
        assert len(n.timer_window(fake))==1
    else:
        with pytest.raises(RuntimeError,match='startup margin'):
            n.timer_window(fake)


def test_only_exact_busy_result_is_admitted():
    base=json.loads((HERE/'plan.json').read_bytes())
    run=json.loads((HERE/'postapply-busy-evidence/run.json').read_bytes())
    root=json.loads((HERE/'postapply-busy-evidence/root-result.json').read_bytes())
    n.validate_busy(m,run,root,base)
    for key,value in [('deleted_this_run',1),('reason','DataError'),('exit_code',0)]:
        changed=copy.deepcopy(run);changed[key]=value
        with pytest.raises(RuntimeError,match='reviewed pre-lock'):
            n.validate_busy(m,changed,root,base)
    root['manual_finalization']='unknown'
    with pytest.raises(RuntimeError):
        n.validate_busy(m,run,root,base)


@pytest.mark.parametrize('fail_start',[False,True])
def test_one_start_no_reload_and_runner_released(tmp_path,monkeypatch,fail_start):
    manual=tmp_path/'manual.json';manual.write_text('{"enabled":false}')
    plan={'operation_plan_id':'new-recovery','manual_before':{'text':manual.read_text(),'fingerprint':h.fingerprint(manual)}}
    out=tmp_path/'audit';out.mkdir()
    contract={'operation_plan_id':'new-recovery','based_on_operation':'prior','files':{'busy_run':{'sha256':'f'*64}}}
    held=[False];commands=[]
    @contextmanager
    def runner():
        assert not held[0];held[0]=True
        try:yield
        finally:held[0]=False
    helper=SimpleNamespace(MANUAL=manual,locks=nullcontext,blob=h.blob,write_new=h.write_new,
        fingerprint=h.fingerprint,replace_checked=h.replace_checked,proposal=lambda:{})
    def command(argv):
        assert argv==['/usr/bin/systemctl','--system','--no-block','start','only-approved.service']
        assert not held[0]
        assert json.loads((out/'state.json').read_bytes())['dispatch_intent']
        commands.append(argv)
        if fail_start:raise RuntimeError('ambiguous transport')
    app=SimpleNamespace(runner_idle=runner,UNIT='only-approved.service',command=command,runs=lambda:{'old':'unused'},
        enabled_grant=lambda *args:{'enabled':True})
    monkeypatch.setattr(n,'preflight',lambda *args:({}, {'ready':True}, []))
    monkeypatch.setattr(n,'seal',lambda *args:out)
    monkeypatch.setattr(m,'scope_transition_proposal',lambda *args:{})
    def finalize(plan,helper,app,out,record):
        assert not held[0] and record['dispatch_intent']
        assert ('start_command_error' in record)==fail_start
        manual.write_text(plan['manual_before']['text'])
        return int(fail_start)
    monkeypatch.setattr(m,'finalize',finalize)
    assert n.operate(m,helper,app,plan,contract,'apply')==int(fail_start)
    with pytest.raises(RuntimeError,match='Recovery consumed'):
        n.operate(m,helper,app,plan,contract,'apply')
    assert len(commands)==1


def test_near_timer_refuses_before_creating_grant_or_intent(tmp_path,monkeypatch):
    @contextmanager
    def noop():yield
    helper=SimpleNamespace(locks=noop)
    app=SimpleNamespace(runner_idle=noop)
    def denied(*args):raise RuntimeError('Preservation timer is due within startup margin')
    monkeypatch.setattr(n,'preflight',denied)
    monkeypatch.setattr(n,'seal',lambda *args:pytest.fail('must not create audit/grant/start yet'))
    with pytest.raises(RuntimeError,match='startup margin'):
        n.operate(m,helper,app,{}, {}, 'apply')
