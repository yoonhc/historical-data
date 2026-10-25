"""Render eight system jobs from explicit paths; never install or start units."""

import argparse
import json
import re
from pathlib import Path

JOBS = (
    "oi",
    "oi-probe",
    "oi-quality",
    "daily",
    "pack",
    "backup",
    "restic-archive",
    "network-observer",
)
TEMPLATES = Path(__file__).resolve().parents[1] / "integrations/systemd/scheduled"


def render(
    *,
    release,
    store_root,
    user,
    user_home,
    config_root="/etc/marketdata",
    archive_profile=None,
    network_profile=None,
    archive_env=None,
    network_env=None,
):
    if user == "root" or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Explicit non-root service user required")
    profile_root = Path(user_home) / ".config/marketdata"
    values = {
        "RELEASE": release,
        "STORE_ROOT": store_root,
        "USER_HOME": user_home,
        "CONFIG_ROOT": config_root,
        "ARCHIVE_PROFILE": archive_profile or profile_root / "restic-archive.toml",
        "NETWORK_PROFILE": network_profile or profile_root / "network-observer.toml",
        "ARCHIVE_ENV": archive_env or profile_root / "restic-service.env",
        "NETWORK_ENV": network_env or profile_root / "network-observer.env",
    }
    for value in values.values():
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(value)) or ".." in Path(value).parts:
            raise ValueError("Explicit safe absolute systemd paths required")
    values["USER"] = user
    names = [f"marketdata-{job}.{suffix}" for job in JOBS for suffix in ("service", "timer")]
    names.append("marketdata-jobs-clock-ready.service")
    result = {}
    for name in names:
        text = (TEMPLATES / name).read_text()
        for key, value in values.items():
            text = text.replace(f"@{key}@", str(value))
        if re.search(r"@[A-Z_]+@", text):
            raise ValueError(f"Unresolved unit input: {name}")
        result[name] = text
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    units = render(**json.loads(args.input.read_text()))
    args.output.mkdir(parents=True, exist_ok=False)
    for name, text in units.items():
        (args.output / name).write_text(text)


if __name__ == "__main__":
    main()
