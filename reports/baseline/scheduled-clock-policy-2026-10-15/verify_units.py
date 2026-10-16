import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
m = json.loads((HERE / "manifest.json").read_text())
root = "/opt/marketdata/releases/" + m["release_id"]
output = HERE / "unit-check"
output.mkdir(exist_ok=True)
files = []
for f in (HERE / "rendered/units").glob("*"):
    p = output / f.name
    p.write_text(f.read_text().replace(root, str(HERE / "candidate")))
    files.append(str(p))
env = {
    **os.environ,
    "SYSTEMD_UNIT_PATH": str(output) + ":/etc/systemd/system:/usr/lib/systemd/system",
}
r = subprocess.run(
    ["/usr/bin/systemd-analyze", "--man=no", "verify", *files],
    env=env,
    capture_output=True,
    text=True,
    timeout=50,
)
record = {
    "returncode": r.returncode,
    "stdout": r.stdout,
    "stderr": r.stderr,
    "units": len(files),
    "uninstalled_path_substitution": {root: str(HERE / "candidate")},
    "actual_system_execution": False,
}
(HERE / "unit-verification.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record))
raise SystemExit(r.returncode)
