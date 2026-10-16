import hashlib
import json
import shlex
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def test_only_eight_jobs_use_new_waiter_and_timers_keep_exact_bytes():
    m = json.loads((HERE / "manifest.json").read_bytes())
    topology = json.loads((HERE / "rendered/topology.json").read_bytes())
    works = [(n, s) for n, s in topology["units"].items() if s["kind"] == "work"]
    assert len(works) == 8
    for name, spec in works:
        assert spec["Requires"] == ["marketdata-jobs-clock-ready.service"]
        assert spec["ExecStartPre"] == [] and spec["User"] == "yoonhc"
        assert "run_job.py" in spec["ExecStart"][0]
        assert "--run --" in spec["ExecStart"][0]
        job = name.removeprefix("marketdata-").removesuffix(".service")
        assert "--job " + job in spec["ExecStart"][0]
        assert "ProtectClock=yes" in (HERE / "rendered/units" / name).read_text()
    for path, sha in m["before"].items():
        if path.endswith(".timer"):
            assert m["after"][path] == sha
    assert not any(
        "chrony" in p or "collector" in p or "retirement.service" in p for p in m["after"]
    )


def test_bootstrap_and_T05_receive_new_binding_without_new_preparation_or_baseline():
    m = json.loads((HERE / "manifest.json").read_bytes())
    p = json.loads((HERE / "rendered/clock-bootstrap.json").read_bytes())
    old = json.loads(Path("/etc/marketdata/clock-bootstrap.json").read_bytes())
    assert {k: v for k, v in p.items() if k != "guard_files"} == {
        k: v for k, v in old.items() if k != "guard_files"
    }
    bind = json.loads((HERE / "rendered/clock-binding.json").read_bytes())
    assert (
        bind["bootstrap_profile_sha256"]
        == hashlib.sha256((HERE / "rendered/clock-bootstrap.json").read_bytes()).hexdigest()
    )
    assert "/etc/marketdata/checkpoint-clock-retirement/clock-binding.json" in m["after"]
    assert not any("boot-state" in p or "baseline" in p or "registry" in p for p in m["after"])


def test_wrapper_dispatch_preserves_exact_work_arguments():
    topology = json.loads((HERE / "rendered/topology.json").read_bytes())
    old = json.loads(Path("/etc/marketdata/scheduled-system/topology.json").read_bytes())
    for name, spec in topology["units"].items():
        if spec["kind"] != "work":
            continue
        argv = shlex.split(old["units"][name]["ExecStart"][0])
        expected = argv[argv.index("-m") + 2 :] if "-m" in argv else argv[4:]
        new = shlex.split(spec["ExecStart"][0])
        assert new[new.index("--") + 1 :] == expected
