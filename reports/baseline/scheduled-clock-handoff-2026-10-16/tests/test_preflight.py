import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import system_preflight as p


@pytest.mark.parametrize("job", p.t.JOBS)
def test_trial_units_preserve_options_and_use_only_the_trial_waiter(job):
    source = HERE.parent / "scheduled-clock-policy-storage-2026-10-16/rendered/units" / ("marketdata-" + job + ".service")
    template = source.read_text()
    trial = Path("/opt/md-t16-0123456789abcdef")
    waiter = "md-t16-0123456789abcdef-ready.service"
    command = str(trial / "candidate/base/.venv/bin/python") + " -I test.py"
    text = p.trial_unit(template, trial, Path("/var/lib") / trial.name / job, "trial.service", waiter, command)
    assert "Requires=" + waiter in text
    assert "marketdata-clock-ready.service" not in text
    assert "ExecStartPre=" not in text
    for key in ("User=", "ProtectClock=", "TimeoutStartSec=", "TimeoutStopSec=", "KillSignal=", "EnvironmentFile=", "UMask=", "Nice=", "IOScheduling"):
        assert [s for s in template.splitlines() if s.startswith(key)] == [s for s in text.splitlines() if s.startswith(key)]
    assert "\nExecStart=\nExecStart=" + command in text


def test_trial_dependency_cannot_reach_an_operating_waiter():
    with pytest.raises(p.c.Hold, match="dependency"):
        p.trial_unit("[Unit]\nRequires=marketdata-clock-ready.service\n", Path("/opt/trial"), Path("/var/lib/trial"), "x", "trial-ready.service", "/usr/bin/true")


def test_mutating_manager_commands_are_exactly_nonce_scoped():
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    m = p.c.Managers(allowed=[("system", "md-t16-trial.service")], runner=runner)
    m.command("system", "start", "--no-block", "md-t16-trial.service")
    for name in ("marketdata-daily.service", "marketdata-collector@depth.service", "chrony.service"):
        with pytest.raises(p.c.Hold, match="scope"):
            m.command("system", "stop", name)
    with pytest.raises(p.c.Hold):
        m.command("system", "enable", "md-t16-trial.service")
    assert len(calls) == 1


def test_fixed_candidate_and_original_transaction_engine_unchanged():
    candidate = HERE.parent / "scheduled-clock-policy-storage-2026-10-16"
    manifest = json.loads((candidate / "manifest.json").read_text())
    for name, digest in manifest["runtime_files"].items():
        assert p.c.sha((candidate / "candidate" / name).read_bytes()) == digest
    assert (HERE / "vendor/transaction_engine.py").read_bytes() == (candidate / "candidate/base/migration/transaction.py").read_bytes()
    assert (HERE / "vendor/handoff_common.py").read_bytes() == (HERE.parent / "scheduled-system-handoff-2026-10-12/handoff_common.py").read_bytes()
