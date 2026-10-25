"""T18 exact configuration handoff; host I/O is injected and separately reviewed.

Drain never holds the coordinator. New business requests are excluded by their
existing locks during the short configuration transaction. No business stop,
request erasure, checkpoint action, or automatic restart of the old writer.
"""

import copy
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "operation_common", Path(__file__).with_name("common.py")
)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

PURPOSE = "t18-verified-output-handoff-v1"
STATE = "/var/lib/marketdata-clock/t18-output-handoffs"
SERVICES = tuple("marketdata-" + j + ".service" for j in c.JOBS) + (c.T05_SERVICE,)
TIMERS = tuple("marketdata-" + j + ".timer" for j in c.JOBS) + (c.T05_TIMER,)


class Hold(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise Hold(message)


def profile_id(text):
    return c.sha(json.dumps(json.loads(text), sort_keys=True).encode())


def validate(plan):
    require(plan["purpose"] == PURPOSE and plan["schema"] == 1, "Unexpected operation")
    require(
        plan["operation_id"]
        == c.sha(c.canonical({k: v for k, v in plan.items() if k != "operation_id"})),
        "Operation digest differs",
    )
    require(
        plan["drain_seconds"] == 600 and plan["resume_timer"] == c.TIMER,
        "Unexpected lifecycle scope",
    )
    require(plan["runtime_timer_holds"] == list(TIMERS), "Unexpected hold targets")
    p = plan["prepared"]
    require(
        p["preparation_id"]
        == c.sha(c.canonical({k: v for k, v in p.items() if k != "preparation_id"})),
        "Preparation differs",
    )
    require(
        set(p["changes"]) == {c.PROFILE, c.TOPOLOGY, c.SERVICE_PATH, c.BINDING, c.T05_BINDING},
        "Unexpected writes",
    )
    for change in p["changes"].values():
        for value in change.values():
            require(
                value["kind"] == "file" and value["mode"] == 0o644, "Unexpected configuration type"
            )
            require(c.sha(value["text"].encode()) == value["sha256"], "Embedded bytes differ")
    before = json.loads(p["recurring_before"]["text"])
    after = p["recurring_proposal_after"]
    require(before["enabled"] is True and after["enabled"] is False, "Wrong recurring boundary")
    old_scope, new_scope = copy.deepcopy(before["scope"]), copy.deepcopy(after["scope"])
    old_scope.pop("clock_binding")
    new_scope.pop("clock_binding")
    require(old_scope == new_scope, "T05 policy/code/path changes are outside this operation")
    require(after["scope_id"] == c.sha(c.canonical(after["scope"])), "Unexpected T05 scope")


class Operation:
    def __init__(self, plan, host, *, fault=lambda point: None):
        validate(plan)
        self.plan, self.h, self.fault = plan, host, fault
        self.p = plan["prepared"]
        self.state = host.read("state.json") or {}

    def save(self, point):
        self.h.save("state.json", self.state)
        self.fault(point)

    def authority(self):
        value = self.h.read("authority.json")
        if value is None:
            require(not self.state, "Missing authority for existing operation")
            value = {
                "purpose": PURPOSE,
                "operation_id": self.plan["operation_id"],
                "approved_at": self.h.now(),
                "scope_id": self.p["recurring_proposal_after"]["scope_id"],
                "actions": ["apply", "stop", "rollback"],
            }
            self.h.save("authority.json", value, immutable=True)
        require(
            value["operation_id"] == self.plan["operation_id"]
            and value["scope_id"] == self.p["recurring_proposal_after"]["scope_id"],
            "Authority differs",
        )
        return value

    def grant(self):
        authority = self.authority()
        return {
            **self.p["recurring_proposal_after"],
            "enabled": True,
            "approved_at": authority["approved_at"],
            "approval_reference": self.h.record_path("authority.json"),
        }

    def known(self):
        self.h.immutable()
        tx = self.h.read("transaction.json")
        ledger = json.loads(self.h.raw(c.LEDGER))
        if tx is None:
            require(
                ledger == self.p["ledger_before"]
                and self.at("before")
                and self.h.raw(c.GRANT) == self.p["recurring_before"]["text"].encode(),
                "Initial ledger/configuration differs",
            )
        else:
            receipt = self.h.read(tx["direction"] + "-receipt.json")
            allowed = [tx["base"], self.pending(tx), tx.get("previous_pending")]
            if receipt is not None:
                allowed.append(receipt["ledger_after"])
            require(ledger in allowed, "Ledger changed outside this operation")
        for path, pair in self.p["changes"].items():
            require(
                self.h.raw(path).decode() in [v["text"] for v in pair.values()],
                "Unrecognized configuration: " + path,
            )
        require(
            self.h.raw(c.GRANT)
            in (
                self.p["recurring_before"]["text"].encode(),
                c.encode(self.p["recurring_proposal_after"]),
                c.encode(self.grant()),
            ),
            "Unrecognized recurring grant",
        )

    def at(self, side):
        return all(
            self.h.raw(path) == pair[side]["text"].encode()
            for path, pair in self.p["changes"].items()
        )

    def hold_and_drain(self):
        self.known()
        if "timers_before" not in self.state:
            self.state["timers_before"] = self.h.timers(TIMERS)
            require(
                self.state["timers_before"][c.TIMER]["ActiveState"] == "inactive",
                "Archiver timer must already be held",
            )
            self.save("timer_inventory")
        self.h.stop_timers(TIMERS)
        self.state["phase"] = "timers_held"
        self.save("timers_held")
        # Deliberately outside both coordinator and request/runner locks.
        self.h.drain(SERVICES, self.plan["drain_seconds"])
        self.fault("drained")

    def pending(self, tx):
        return {
            "schema": 2,
            "purpose": PURPOSE,
            "operation_id": self.plan["operation_id"],
            "direction": tx["direction"],
            "preserved": tx["base"],
        }

    def configure(self, direction):
        side = "after" if direction == "apply" else "before"
        with self.h.transaction_locks():
            self.known()
            self.h.idle(SERVICES, TIMERS)
            self.h.terminal_requests()
            tx = self.h.read("transaction.json")
            ledger = json.loads(self.h.raw(c.LEDGER))
            receipt = self.h.read(direction + "-receipt.json")
            expected_grant = (
                c.encode(self.grant())
                if direction == "apply"
                else self.p["recurring_before"]["text"].encode()
            )
            if receipt is not None and ledger == receipt["ledger_after"]:
                require(
                    self.at(side) and self.h.raw(c.GRANT) == expected_grant,
                    "Committed configuration drifted",
                )
                return
            if tx is None:
                require(
                    direction == "apply"
                    and ledger == self.p["ledger_before"]
                    and self.at("before"),
                    "Baseline differs",
                )
                tx = {"direction": direction, "base": ledger}
                self.h.save("transaction.json", tx)
                self.fault("transaction_intent")
            elif tx["direction"] != direction:
                require(direction == "rollback", "Rollback cannot be reapplied")
                apply_receipt = self.h.read("apply-receipt.json")
                committed = apply_receipt is not None and ledger == apply_receipt["ledger_after"]
                require(
                    committed or ledger in (tx["base"], self.pending(tx)), "Rollback ledger differs"
                )
                tx = {
                    "direction": direction,
                    "base": ledger if committed else tx["base"],
                    "previous_pending": self.pending(tx),
                }
                self.h.save("transaction.json", tx)
                self.fault("rollback_intent")
            require(
                ledger in (tx["base"], self.pending(tx), tx.get("previous_pending")),
                "Ledger changed outside transition",
            )
            self.h.write(c.LEDGER, c.encode(self.pending(tx)))
            self.fault("pending")
            # Close recurring authority before replacing any scope input. The
            # runner lock also excludes manual/automatic T05 bodies throughout.
            self.h.write(c.GRANT, c.encode(self.p["recurring_proposal_after"]))
            self.fault("grant_disabled")
            for path, pair in sorted(self.p["changes"].items()):
                self.h.write(path, pair[side]["text"].encode())
                self.fault("file:" + path)
            self.h.reload_and_verify(side)
            expected = (
                self.p["recurring_proposal_after"]
                if direction == "apply"
                else {
                    **json.loads(self.p["recurring_before"]["text"]),
                    "enabled": False,
                    "approved_at": None,
                    "approval_reference": None,
                }
            )
            require(
                self.h.propose() == expected, "Actual full proposal differs; grant remains disabled"
            )
            self.fault("proposal_verified")
            self.h.write(c.GRANT, expected_grant)
            self.fault("grant_restored")
            final = copy.deepcopy(tx["base"])
            final["profile_sha256"] = profile_id(self.p["changes"][c.PROFILE][side]["text"])
            history = final.setdefault("configuration_history", [])
            history.append(
                {
                    "plan_id": self.plan["operation_id"],
                    "direction": direction,
                    "receipt": self.h.record_path(direction + "-receipt.json"),
                }
            )
            receipt = {
                "operation_id": self.plan["operation_id"],
                "direction": direction,
                "ledger_after": final,
                "clock_control": False,
                "records_preserved": True,
            }
            self.h.save(direction + "-receipt.json", receipt, immutable=True)
            self.fault("receipt")
            self.h.write(c.LEDGER, c.encode(final))
            self.fault("ledger_committed")
        self.state["phase"] = direction + "_committed"
        self.save("configuration_committed")

    def start_once(self, name, namespace):
        starts = self.state.setdefault(namespace, {})
        current = self.h.timers((name,))[name]
        if name in starts:
            entry = starts[name]
            if entry.get("returned"):
                require(
                    current["ActiveState"] == "active",
                    "Previously started timer is no longer active",
                )
                return
            positive = (
                current["ActiveState"] == "active"
                and current.get("InvocationID")
                and current["InvocationID"] != entry["before"].get("InvocationID")
            )
            require(positive, "Start issuance is ambiguous; do not repeat: " + name)
            entry.update(returned=True, reconciled_from_manager=True, after=current)
            self.save("start_reconciled")
            return
        require(
            current["ActiveState"] == "inactive" and not current.get("Job"),
            "Unexpected timer state",
        )
        starts[name] = {"before": current}
        self.save("start_intent:" + name)
        self.h.start_timer(name)
        self.fault("start_return:" + name)
        starts[name].update(returned=True, after=self.h.timers((name,))[name])
        require(starts[name]["after"]["ActiveState"] == "active", "Timer did not activate")
        self.save("start_recorded:" + name)

    def restore_other_timers(self, direction):
        for name, before in self.state["timers_before"].items():
            if name != c.TIMER and before["ActiveState"] == "active":
                self.start_once(name, direction + "_starts")

    def run(self, action):
        require(action in {"apply", "rollback", "stop"}, "Unknown action")
        if action == "apply":
            require(not self.state.get("closed"), "Stopped/reverted approval cannot reactivate")
            if self.state.get("phase") == "activated":
                self.h.verify_activated(self.grant())
                return self.state
            self.authority()
            self.known()
            self.h.capacity()
            if not self.state.get("installed"):
                self.h.install()
                self.state["installed"] = True
                self.save("installed")
            # A restart after commit must not stop already restored timers.
            receipt = self.h.read("apply-receipt.json")
            committed = (
                receipt is not None and json.loads(self.h.raw(c.LEDGER)) == receipt["ledger_after"]
            )
            if not committed:
                self.hold_and_drain()
                self.configure("apply")
            else:
                require(
                    self.at("after") and self.h.raw(c.GRANT) == c.encode(self.grant()),
                    "Committed files differ",
                )
            self.h.readonly_check()
            self.h.capacity()
            self.restore_other_timers("apply")
            self.start_once(c.TIMER, "apply_starts")
            self.h.verify_activated(self.grant())
            self.state.update(phase="activated", operating_observation_pending=True)
            self.save("activated")
        elif action == "stop":
            require(self.h.read("authority.json") is not None, "No accepted operation to stop")
            self.state["closed"] = "stop"
            self.save("stop_intent")
            self.h.stop_timers((c.TIMER,))
            self.h.drain((c.SERVICE,), self.plan["drain_seconds"])
            self.state["phase"] = "archiver_timer_held"
            self.save("stopped")
        else:
            require(self.h.read("authority.json") is not None, "No accepted operation to rollback")
            if self.state.get("phase") == "rolled_back_archiver_held":
                require(
                    self.at("before")
                    and self.h.raw(c.GRANT) == self.p["recurring_before"]["text"].encode(),
                    "Rollback configuration drifted",
                )
                require(
                    self.h.timers((c.TIMER,))[c.TIMER]["ActiveState"] == "inactive",
                    "Old writer timer must remain held",
                )
                return self.state
            self.state["closed"] = "rollback"
            self.save("rollback_requested")
            receipt = self.h.read("rollback-receipt.json")
            committed = (
                receipt is not None and json.loads(self.h.raw(c.LEDGER)) == receipt["ledger_after"]
            )
            if not committed:
                self.hold_and_drain()
                if self.h.read("transaction.json") is None:
                    # Installation/drain may have stopped before any ledger or
                    # configuration write. Restore prior timer requests only.
                    require(
                        self.at("before")
                        and json.loads(self.h.raw(c.LEDGER)) == self.p["ledger_before"]
                        and self.h.raw(c.GRANT) == self.p["recurring_before"]["text"].encode(),
                        "Unchanged abort boundary differs",
                    )
                    self.h.save(
                        "abort-before-configuration.json",
                        {
                            "operation_id": self.plan["operation_id"],
                            "configuration_writes": 0,
                            "ledger_preserved": True,
                        },
                        immutable=True,
                    )
                else:
                    self.configure("rollback")
            else:
                require(
                    self.at("before")
                    and self.h.raw(c.GRANT) == self.p["recurring_before"]["text"].encode(),
                    "Rollback files differ",
                )
            self.h.readonly_check(side="before")
            self.restore_other_timers("rollback")
            require(
                self.h.timers((c.TIMER,))[c.TIMER]["ActiveState"] == "inactive",
                "Old writer timer must remain held",
            )
            self.state["phase"] = "rolled_back_archiver_held"
            self.save("rolled_back")
        return self.state
