"""Install the pinned official restic Linux amd64 release outside the Python environment."""

import argparse
import bz2
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

VERSION = "0.19.1"
ASSET = f"restic_{VERSION}_linux_amd64.bz2"
ASSET_SHA256 = "f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c"
SUMS_SHA256 = "fb520966ee01d2a3d4219c66762c66efa56300833b6f639f36082b7462f91cb8"
BASE = f"https://github.com/restic/restic/releases/download/v{VERSION}/"


def download(name):
    request = Request(BASE + name, headers={"User-Agent": "marketdata-restic-trial"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def install(destination):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("This pinned installer supports Linux x86_64 only")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new executable path")
    sums, compressed = download("SHA256SUMS"), download(ASSET)
    if hashlib.sha256(sums).hexdigest() != SUMS_SHA256:
        raise ValueError("Official checksum file differs from the pinned release")
    expected = next(line.split()[0] for line in sums.decode().splitlines() if line.endswith(ASSET))
    if hashlib.sha256(compressed).hexdigest() != expected or expected != ASSET_SHA256:
        raise ValueError("Official binary archive checksum mismatch")
    binary = bz2.decompress(compressed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.write(binary)
    destination.chmod(0o755)
    version = subprocess.check_output([str(destination), "version"], text=True).strip()
    if not version.startswith(f"restic {VERSION} "):
        raise ValueError("Installed executable has an unexpected version")
    return {
        "version": version,
        "path": str(destination),
        "url": BASE + ASSET,
        "archive_sha256": expected,
        "binary_sha256": hashlib.sha256(binary).hexdigest(),
        "checksum_verification": "pinned GitHub release API digests and official SHA256SUMS over HTTPS",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.output), indent=2))
