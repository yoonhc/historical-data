"""Exact reviewed-run recovery checks; all mutation and locks use temporary paths."""
import copy
import fcntl
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


app = module('manual_resume', HERE / 'manual_resume.py')
helper = module('scope_helper', HERE.parent / 't05-disabled-scope-2026-10-17/refresh_scope.py')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.proposal = json.loads((HERE / 'manual-proposal.json').read_bytes())
        self.busy = json.loads((HERE / 'evidence/busy-run/run.json').read_bytes())
        self.idle = {'ActiveState':'inactive', 'MainPID':'0', 'ControlPID':'0', 'Job':''}

    def test_proposal_differs_only_in_explicit_recovery_decision(self):
        original = json.loads((HERE.parent / 't05-first-manual-recovery-2026-10-17/manual-proposal.json').read_bytes())
        self.assertEqual(self.proposal, {**original, 'resume_interrupted':True})
        original = copy.deepcopy(self.proposal)
        enabled = app.enabled_grant(self.proposal, '2026-10-17T07:00:00+00:00', 'explicit-recovery-approval')
        self.assertEqual(enabled, {**self.proposal, 'enabled':True,
                                  'approved_at':'2026-10-17T07:00:00+00:00',
                                  'approval_reference':'explicit-recovery-approval'})
        self.assertEqual(self.proposal, original)

    def test_first_manual_approval_alone_does_not_enable_recovery(self):
        app.approval_confirmed(False, None, None)
        app.approval_confirmed(True, app.PLAN_ID, app.BUSY_RUN)
        for pid, rid in ((app.PLAN_ID,None), (app.PLAN_ID,app.FAILED_RUN), ('0'*64,app.BUSY_RUN)):
            with self.subTest(pid=pid,rid=rid), self.assertRaises(RuntimeError):
                app.approval_confirmed(True,pid,rid)

    def test_changed_plan_scope_boot_resume_or_action_is_rejected(self):
        for key,val in [('plan_id','0'*64),('scope_id','0'*64),('approved_boot_id','other'),
                        ('resume_interrupted',False),('action','run'),('enabled',True)]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                app.validate_grant({**self.proposal,key:val},self.proposal)
        with self.assertRaises(RuntimeError):
            app.enabled_grant(self.proposal, '2026-10-17T07:00:00Z', '')

    def test_only_reviewed_busy_boundary_and_zero_deletions_are_accepted(self):
        app.reviewed_busy_failure(self.busy)
        for key,val in [('run_id','other'),('plan_id','0'*64),('reason','ClockHold'),
                        ('detail','other'),('state','failed'),('exit_code',1),('deleted_this_run',1),
                        ('deleted_checkpoints',1),('completed_before_run',1),('deletion_attempted',False),
                        ('retirement_journal','exists'),('result',{})]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                app.reviewed_busy_failure({**self.busy,key:val})

    def test_history_rejects_new_attempt_or_missing_or_changed_evidence(self):
        original = json.loads((HERE.parent / 't05-first-manual-recovery-2026-10-17/original-attempt/run.json').read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            paths={}; inputs={'files':{}}
            for label,value in [('failed_run',original),('busy_run',self.busy)]:
                p=Path(tmp)/value['run_id']/'run.json';p.parent.mkdir();p.write_text(json.dumps(value))
                inputs['files'][label]={'path':str(p),'sha256':app.sha(p.read_bytes())}; paths[value['run_id']]=p
            for label,rid in [('failed_root_result',app.FAILED_RUN),('busy_root_result',app.BUSY_RUN)]:
                p=Path(tmp)/(label+'.json');p.write_text(json.dumps({'run_id':rid,'passed':False,
                    'manual_finalization':'original_disabled_restored','protected_inputs_and_processes_unchanged':True}))
                inputs['files'][label]={'path':str(p),'sha256':app.sha(p.read_bytes())}
            with patch.object(app,'runs',return_value=paths):
                app.reviewed_history(inputs)
                paths[app.BUSY_RUN].write_text(json.dumps({**self.busy,'detail':'changed'}))
                with self.assertRaises(RuntimeError):app.reviewed_history(inputs)
            with patch.object(app,'runs',return_value={app.FAILED_RUN:paths[app.FAILED_RUN]}):
                with self.assertRaises(RuntimeError):app.reviewed_history(inputs)
            extra=Path(tmp)/'new'/'run.json';extra.parent.mkdir();extra.write_text(json.dumps({**self.busy,'run_id':'new'}))
            with patch.object(app,'runs',return_value={'new':extra}):
                with self.assertRaises(RuntimeError):app.reviewed_history(inputs)

    def test_any_journal_inventory_staging_or_dangling_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state';stage=Path(tmp)/'stage'
            with patch.object(app,'STATE',state),patch.object(app,'STAGING',stage):
                app.no_mutation_artifacts()
                targets=[state/'retention/journals'/(app.PLAN_ID+'.json'),
                         state/'retention/inventories'/app.PLAN_ID,stage/app.PLAN_ID]
                for p in targets:
                    p.parent.mkdir(parents=True,exist_ok=True)
                    p.symlink_to(p.parent/'missing')
                    with self.assertRaises(RuntimeError):app.no_mutation_artifacts()
                    p.unlink()

    def test_manifest_or_root_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'copy';root.mkdir();marker=root/'backup.json';marker.write_text('{}')
            s=root.stat();entry={'path':str(root),'manifest_sha256':app.sha(marker.read_bytes())}
            plan={'candidates':[{'entry':entry,'root':{'dev':s.st_dev,'ino':s.st_ino,'mode':s.st_mode}}],
                  'keep':[entry], 'excluded':[entry]}
            app.untouched_roots(plan)
            marker.write_text('{"changed":true}')
            with self.assertRaises(RuntimeError):app.untouched_roots(plan)
            marker.write_text('{}');root.chmod(0o700)
            with self.assertRaises(RuntimeError):app.untouched_roots(plan)

    def test_actual_flock_contention_blocks_then_releases_without_holding_service_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            maintenance=Path(tmp)/'maintenance';archive=Path(tmp)/'archive'
            maintenance.touch();archive.touch()
            def show(argv):return '\n'.join(f'{k}={v}' for k,v in {'Id':argv[3],**self.idle}.items())
            with patch.object(app,'MAINTENANCE_LOCK',maintenance),patch.object(app,'REPOSITORY_LOCK',archive),patch.object(app,'command',side_effect=show):
                with archive.open('rb') as fd:
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    with self.assertRaises(BlockingIOError):app.check_preservation_idle()
                app.check_preservation_idle()
                for p in (maintenance,archive):
                    with p.open('rb') as fd:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_pending_job_or_active_preservation_work_is_rejected(self):
        for change in ({'ActiveState':'activating'},{'MainPID':'12'},{'Job':'99'}):
            def show(argv):return '\n'.join(f'{k}={v}' for k,v in {'Id':argv[3],**self.idle,**change}.items())
            with patch.object(app,'command',side_effect=show),self.assertRaises(RuntimeError):
                app.check_preservation_idle()

    def test_disabled_grant_restored_only_after_idle_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'manual.json';path.write_text('{"enabled":false}\n');path.chmod(0o644)
            original={'text':path.read_text(),'fingerprint':helper.fingerprint(path)}
            raw='{"enabled":true,"resume_interrupted":true}\n'
            enabled={'text':raw,'fingerprint':{**original['fingerprint'],'sha256':app.sha(raw.encode())}}
            with patch.object(helper,'MANUAL',path):
                helper.replace_checked(path,original['fingerprint'],enabled,lambda:None)
                with self.assertRaises(RuntimeError):
                    app.restore_when_idle(helper,original,enabled,lambda:{**self.idle,'MainPID':'2'})
                self.assertEqual(helper.fingerprint(path),enabled['fingerprint'])
                self.assertEqual(app.restore_when_idle(helper,original,enabled,lambda:self.idle),'original_disabled_restored')
                self.assertEqual(path.read_text(),original['text'])
                path.write_text('changed')
                with self.assertRaises(RuntimeError):app.restore_when_idle(helper,original,enabled,lambda:self.idle)


class InstalledRecoveryGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        release=Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
        sys.path[:0]=[str(release),str(release/'packages/marketdata/src')]
        from scripts import clock_retirement
        cls.r=clock_retirement

    @contextmanager
    def fixture(self,resume,other_plan=False):
        r=self.r
        @contextmanager
        def unlocked(_):yield
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prior=json.loads((HERE/'evidence/busy-run/run.json').read_bytes())
            if other_plan:prior['plan_id']='0'*64
            p=root/'runs'/'prior'/'run.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(prior))
            obj=object.__new__(r.ClockRetirement)
            obj.report={'run_id':'new'};obj.settings=SimpleNamespace(state_root=root)
            obj.policy=object();obj.p=SimpleNamespace(reports_root=root/'archive')
            obj.action='apply-plan';obj.manual_plan=app.PLAN_ID;obj.day='2026-10-17'
            obj.guard=SimpleNamespace(check=lambda **_:None)
            obj.journals=lambda:[];obj.a=SimpleNamespace(state={'checkpoints':{}})
            with patch.object(r.retirement.Retirement,'locks',unlocked),patch.object(r,'authorize',return_value={'resume_interrupted':resume}),patch.object(r,'now',return_value=datetime(2026,10,17,7,tzinfo=timezone.utc)),patch.object(r.archive,'discover',return_value=([],[])),patch.object(r.legacy,'daily_evidence',return_value={'fixture':True}):
                yield obj

    def test_installed_prior_run_guard_blocks_first_manual_grant(self):
        with self.fixture(False) as obj:
            with self.assertRaises(self.r.legacy.Deferred) as exc:
                with obj.locks():self.fail('unexpected entry')
            self.assertEqual(exc.exception.reason,'unfinished_retirement')

    def test_installed_guard_accepts_explicit_same_plan_recovery(self):
        with self.fixture(True) as obj:
            with obj.locks():self.assertEqual(obj.report['daily_evidence'],{'fixture':True})

    def test_installed_recovery_does_not_allow_other_plan_history(self):
        with self.fixture(True,other_plan=True) as obj:
            with self.assertRaises(self.r.legacy.Deferred):
                with obj.locks():self.fail('unexpected entry')


if __name__=='__main__':unittest.main(verbosity=2)
