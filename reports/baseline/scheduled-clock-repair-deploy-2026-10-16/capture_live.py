"""Read-only native-host inventory. Writes only this report directory."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "vendor")]
import handoff_common as c
import transition as t


def capture():
    m = c.Managers()  # empty mutation allowlist
    inner = json.loads((HERE / "configuration-plan.json").read_bytes())
    result = {"boot_id": c.boot(), "operations": {}, "effective": {}, "user_states": {},
              "protected_metadata": {}, "ledger": json.loads(Path(t.LEDGER).read_bytes())}
    for path, op in inner["operations"].items():
        p = Path(path)
        actual = c.metadata(p)
        if op["before"]["kind"] == "absent":
            c.require(actual["kind"] == "absent", "Unexpected installed target")
        else:
            c.require(c.sha(p.read_bytes()) == op["before"]["sha256"], "Configuration changed")
            c.require(actual == {"kind": "file", "uid": 0, "gid": 0, "mode": 0o644}, "Before ownership differs")
        result["operations"][path] = actual
    for path in inner["protected"]:
        result["protected_metadata"][path] = c.metadata(path)
    for key in inner["retirement_masks"]:
        manager, name = key.split(":")
        path = (c.SYSTEM_ROOT if manager == "system" else c.USER_ROOT) + name
        result["protected_metadata"][path] = c.metadata(path)
    for name in (*c.SERVICES, *c.TIMERS, c.WAITER, Path(t.WAITER).name, "marketdata-clock-bootstrap.service"):
        result["effective"][name] = m.show("system", name)
    result["system_states"] = m.states("system", (*c.SERVICES, *c.TIMERS))
    result["user_states"] = m.states("user", (*c.SERVICES, *c.TIMERS))
    result["processes"] = {name: c.parse(m.command("system", "show", name,
         "--property=MainPID,InvocationID,ActiveState").stdout) for name in c.IDENTITIES}
    try:
        result["requests"] = t.pending_requests("/home/yoonhc/projects/algoTrade/historical_data/state/scheduled-clock-policy")
    except BlockingIOError:
        # Inventory is advisory while normal jobs keep running. The executor
        # requires a complete locked inventory after timers are held and drain.
        result["requests"] = {"inventory_incomplete": "running job owns request lock"}
    c.require(result["boot_id"] == inner["boot_id"] and result["ledger"] == inner["ledger_before"], "Boot/ledger changed")
    (HERE / "host-before.json").write_bytes(c.encode(result))
    print(json.dumps({"captured": True, "boot_id": result["boot_id"], "system_states": {
        k: v["classification"] for k, v in result["system_states"].items()}, "requests": result["requests"]}))


if __name__ == "__main__":
    capture()
