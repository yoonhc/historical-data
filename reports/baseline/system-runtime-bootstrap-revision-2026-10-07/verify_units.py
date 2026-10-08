"""Offline transaction verification with candidate drop-ins plus distro base units.

No daemon-reload, start, stop, system configuration installation or clock change.
Executable paths only are substituted for binaries not installed on this host.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--chrony-package", required=True, type=Path)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="md-clock-unit-verify-"))
    runtime = json.loads((args.report / "machine-profile.json").read_text())
    boot = json.loads((args.report / "boot-profile.json").read_text())
    original_root = runtime["release_root"]
    shutil.copytree(args.report / "units", root, dirs_exist_ok=True)
    source_hashes = {}
    distro = args.chrony_package / "usr/lib/systemd/system"
    for name in ("chrony.service", "chrony-wait.service"):
        p = distro / name
        source_hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        shutil.copyfile(p, root / "system" / name)
    (root / "system/chronyd.service").symlink_to("chrony.service")
    for name, mode in boot["user_units"].items():
        if mode == "guarded":
            p = args.source / "integrations/systemd" / name
            source_hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
            shutil.copyfile(p, root / "user" / name)
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text().replace(original_root, str(args.source))
        text = text.replace("/usr/bin/chronyc", str(args.chrony_package / "usr/bin/chronyc"))
        text = text.replace("/usr/sbin/chronyd", str(args.chrony_package / "usr/sbin/chronyd"))
        path.write_text(text)
    for stream in ("depth", "aggtrade"):
        shutil.copyfile(root / "system/marketdata-collector@.service",
                        root / f"system/marketdata-collector@{stream}.service")
    results = {"fixture": str(root), "substitutions": "release Python and absent chrony executable paths only",
               "base_unit_sha256": source_hashes, "operational_activation": False,
               "root_protection_exercised": False, "checks": []}
    for manager, names in [
        ("system", ["marketdata-clock-bootstrap.service", "marketdata-collector@depth.service",
                    "marketdata-collector@aggtrade.service", "chrony.service", "chrony-wait.service"]),
        ("user", [n for n, mode in boot["user_units"].items() if mode in {"guarded", "waiter"}]),
    ]:
        env = {**os.environ, "SYSTEMD_UNIT_PATH": str(root / manager) + ":", "LC_ALL": "C"}
        argv = ["systemd-analyze", "--" + manager, "verify", "--man=no", *[str(root / manager / n) for n in names]]
        p = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=45)
        results["checks"].append({"argv": argv, "exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr})
    config = args.source / "integrations/runtime/chrony.conf"
    p = subprocess.run([str(args.chrony_package / "usr/sbin/chronyd"), "-p", "-f", str(config)],
                       text=True, capture_output=True, timeout=10)
    results["chrony_parse"] = {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    results["passed"] = p.returncode == 0 and all(c["exit_code"] == 0 and not c["stderr"] for c in results["checks"])
    (args.report / "unit-verify.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": results["passed"], "checks": [{"exit_code": c["exit_code"], "stderr": c["stderr"]} for c in results["checks"]]}))
    raise SystemExit(0 if results["passed"] else 1)


if __name__ == "__main__":
    main()
