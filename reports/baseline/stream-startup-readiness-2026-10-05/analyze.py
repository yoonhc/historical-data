"""Derive startup order from saved journal monotonic timestamps."""

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = OUT.parent / "depth-status-followup-2026-10-05"


def read(name):
    return json.loads((OUT / name).read_bytes())


def units(raw):
    result = {}
    for block in raw.strip().split("\n\n"):
        item = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        result[item["Id"]] = item
    return result


def main():
    host = read("host.json")
    rows = [json.loads(line) for line in gzip.decompress((OUT / "startup-system.jsonl.gz").read_bytes()).splitlines()]
    predicates = {
        "depth_started": lambda m: m.startswith("Started marketdata-depth.service"),
        "aggtrade_started": lambda m: m.startswith("Started marketdata-aggtrade.service"),
        "network_wait_finished": lambda m: m.startswith("Finished NetworkManager-wait-online.service"),
        "initial_ntp_sync": lambda m: m.startswith("Initial clock synchronization"),
    }
    events = {}
    for name, predicate in predicates.items():
        found = [r for r in rows if isinstance(r.get("MESSAGE"), str) and predicate(r["MESSAGE"])]
        assert len(found) == 1, (name, len(found))
        row = found[0]
        events[name] = {"message": row["MESSAGE"], "boot_id": row["_BOOT_ID"],
                        "monotonic_us": int(row["__MONOTONIC_TIMESTAMP"]),
                        "recorded_wall_at": datetime.fromtimestamp(int(row["__REALTIME_TIMESTAMP"]) / 1e6, timezone.utc).isoformat()}
    assert len({e["boot_id"] for e in events.values()}) == 1
    user = units(host["commands"]["user_units"]["stdout"])
    system = units(host["commands"]["system_units"]["stdout"])
    assert user["network-online.target"]["LoadState"] == "not-found"
    assert system["NetworkManager-wait-online.service"]["UnitFileState"] == "enabled"
    assert system["systemd-time-wait-sync.service"]["UnitFileState"] == "disabled"
    deltas = {name: (event["monotonic_us"] - events["depth_started"]["monotonic_us"]) / 1e6
              for name, event in events.items()}
    assert 0 < deltas["network_wait_finished"] < deltas["initial_ntp_sync"]
    unchanged = {}
    for name, item in host["files"].items():
        unchanged[name] = hashlib.sha256(Path(name).read_bytes()).hexdigest() == item["sha256"]
    assert all(unchanged.values())
    deploy = json.loads((PRIOR.parent / "depth-status-release-2026-10-05/release.json").read_bytes())
    deployed_unchanged = {r["path"]: hashlib.sha256((ROOT / r["path"]).read_bytes()).hexdigest() == r["after_sha256"]
                          for r in deploy["changed_files"]}
    assert all(deployed_unchanged.values())
    offsets = [s["rtc_minus_system_seconds"] for s in read("rtc-observation.json")["samples"]]
    evidence = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (OUT / "host.json", OUT / "startup-system.jsonl.gz", OUT / "startup-user.jsonl.gz",
                          OUT / "rtc-observation.json", OUT / "kernel-rtc.json", PRIOR / "clock-data-boundary.json")}
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "events": events,
              "seconds_after_depth_start": deltas, "user_units": user, "system_units": system,
              "rtc_offset_seconds": {"min": min(offsets), "max": max(offsets), "device_resolution_seconds": 1},
              "configuration_files_unchanged": unchanged, "deployed_patch_files_unchanged": deployed_unchanged,
              "source_evidence_sha256": evidence, "new_network_probes": 0, "services_changed": [],
              "finding": "User collectors started before system network readiness and first NTP synchronization; configured local RTC remains about 41 seconds ahead.",
              "unknowns": ["How the RTC originally acquired the offset", "Why these initial NTP responses timed out", "Whether another installed OS requires local-time RTC"]}
    with (OUT / "findings.json").open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"seconds_after_depth_start": deltas, "rtc_offset_seconds": result["rtc_offset_seconds"],
                      "configurations_unchanged": all(unchanged.values()), "deployed_patch_unchanged": all(deployed_unchanged.values())}))


if __name__ == "__main__":
    main()
