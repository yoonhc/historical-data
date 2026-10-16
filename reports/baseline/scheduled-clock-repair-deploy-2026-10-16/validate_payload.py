"""Copy the exact offline payload to a temporary path and verify each variant.

No operating installation. The temporary directory created here is removed on
exit; interpreter/module/encoder checks and hashes are retained in the report.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from installation import install


SCRIPT = r"""
import hashlib, importlib.util, json, os, sys
from decimal import Decimal
from pathlib import Path
import marketdata, marketdata.cli, marketdata.job_clock
release=Path(sys.argv[1]); variant=sys.argv[2]
result={'uid':os.getuid(),'executable':sys.executable,'prefix':sys.prefix,'modules':{}}
assert sys.prefix==str(release/variant/'.venv')
for mod in (marketdata,marketdata.cli,marketdata.job_clock):
    p=Path(mod.__file__).resolve()
    assert p.is_relative_to(release/variant/'.venv')
    result['modules'][mod.__name__]={'path':str(p.relative_to(release)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
for name in ('run_job','request_contract'):
    spec=importlib.util.spec_from_file_location(name,release/'policy'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    value=json.loads(mod.encode({'decimal':Decimal('1234567890.000000000000000001'),'zero':Decimal('0.000')}))
    assert value=={'decimal':'1234567890.000000000000000001','zero':'0.000'}
result['both_encoders_passed']=True
print(json.dumps(result))
"""


def main():
    payload = json.loads((HERE / "payload.json").read_bytes())
    entries = {
        name.removeprefix("candidate/"): value
        for name, value in payload.items()
        if name.startswith("candidate/")
    }
    records = []
    with tempfile.TemporaryDirectory(prefix="t16-repair-relocated-") as folder:
        release = Path(folder) / "release"
        installed = install(release, entries, identity="isolated-relocation", live=False)
        for variant in ("base", "daily", "preserve"):
            command = [
                str(release / variant / ".venv/bin/python"),
                "-B",
                "-I",
                "-c",
                SCRIPT,
                str(release),
                variant,
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            record = json.loads(result.stdout)
            for info in record["modules"].values():
                assert info["sha256"] == entries[info["path"]]["sha256"]
            records.append({"variant": variant, **record})
        trial_path = str(release)
    result = {
        "passed": True,
        "actual_systemd": False,
        "operating_installation": False,
        "objects": installed["objects"],
        "temporary_path": trial_path,
        "temporary_path_absent": not Path(trial_path).exists(),
        "variants": records,
    }
    (HERE / "relocated-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "variants": len(records),
                "objects": installed["objects"],
                "temporary_path_absent": True,
            }
        )
    )


if __name__ == "__main__":
    main()
