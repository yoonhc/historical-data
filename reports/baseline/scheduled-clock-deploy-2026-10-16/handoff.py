"""Exact T16 system-to-system handoff. Default command inspects; no implicit approval.

Timer hold and normal drain happen with the existing closed ledger/files intact.
Only after a locked, quiescent recheck is pending durable and the eight temporary
service masks installed. No executing work, collector or old waiter is stopped.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "vendor")]
import handoff_common as c
import systemd_read as reader
import transition as t

PURPOSE = "scheduled-clock-host-handoff-v1"
STATE = "/var/lib/marketdata-clock/scheduled-policy-handoffs"
REQUESTS = "/home/yoonhc/projects/algoTrade/historical_data/state/scheduled-clock-policy"
MASK = {"kind": "symlink", "target": "/dev/null"}
NEW_WAITER = Path(t.WAITER).name
PHASES = ("new", "installed", "timers_held", "sealed", "drained", "staged", "committed", "activated", "complete")


def validate(plan, approval):
    c.require(plan.get("purpose") == PURPOSE and plan.get("policy_changes") == t.POLICY, "Wrong host scope")
    c.require(plan["plan_id"] == c.sha(c.encode({k: v for k, v in plan.items() if k != "plan_id"})), "Host plan hash differs")
    c.require(approval.get("enabled") is True and approval.get("purpose") == PURPOSE
              and approval.get("plan_id") == plan["plan_id"] and approval.get("approval_reference")
              and set(approval.get("directions", [])) == {"apply", "rollback"}, "Explicit exact-plan approval required")
    c.require(plan["inner"] == json.loads((HERE / "configuration-plan.json").read_bytes()), "Configuration plan differs")
    c.require(plan["boot_id"] == plan["inner"]["boot_id"], "Boot binding differs")
    c.require(plan["request_root"] == REQUESTS and plan["drain_seconds"] == 600, "Unexpected handoff settings")


class Host:
    def __init__(self, plan, fs, managers):
        self.plan, self.fs, self.m = plan, fs, managers

    def immutable(self):
        c.require(c.boot() == self.plan["boot_id"], "Boot changed; explicit recovery review required")
        for path, expected in self.plan["inner"]["protected"].items():
            actual = self.fs.node(path)
            if expected["kind"] == "digest":
                c.require(actual.get("sha256") == expected["sha256"], "Protected file changed: " + path)
            else:
                c.require(actual == expected, "Protected link changed: " + path)
        for key, expected in self.plan["inner"]["retirement_masks"].items():
            manager, name = key.split(":")
            path = (c.SYSTEM_ROOT if manager == "system" else c.USER_ROOT) + name
            c.require(self.fs.node(path) == expected == MASK, "Retirement mask changed")
        for path, digest in self.plan["external_files"].items():
            c.require(c.sha(self.fs.path(path).read_bytes()) == digest, "Workload input changed: " + path)
        for path, expected in self.plan["protected_metadata"].items():
            c.require(c.metadata(self.fs.path(path)) == expected, "Protected ownership changed: " + path)

    def auxiliary_idle(self):
        # Both managers matter even though the 16 old user entrypoints stay masked.
        for manager in ("user", "system"):
            states = self.m.states(manager, (*c.SERVICES, *c.TIMERS)) if manager == "user" else {}
            c.require(all(v["classification"] == "idle" for v in states.values()), "Old user job is not idle")
            raw = self.m.command(manager, "list-units", "marketdata-checkpoint-retirement*",
                                 "--all", "--no-legend", "--no-pager", "--plain").stdout
            for line in raw.splitlines():
                name = line.split()[0]
                c.require(c.classify(self.m.show(manager, name), self.m.jobs(manager)) == "idle", "T05 is running")

    def drained(self):
        self.immutable()
        self.auxiliary_idle()
        c.require(all(v["classification"] == "idle" for v in self.m.states("system", (*c.SERVICES, *c.TIMERS)).values()),
                  "Work or queued job is not drained")

    def clock_ready(self):
        # A pending ledger is intentionally not ready. The exact new/old query is
        # performed after commit, outside the exclusive coordinator section.
        return None

    def activation(self, direction):
        side = "after" if direction == "apply" else "before"
        self.drained()
        result = {}
        topology = json.loads(self.plan["inner"]["operations"][t.TOPOLOGY][side]["text"])
        for name, spec in topology["units"].items():
            p = self.m.show("system", name)
            reader.verify(p, spec, quiescent=spec["kind"] == "work")
            if direction == "apply" and name in c.SERVICES:
                c.require(c.WAITER not in p.get("Requires", "").split()
                          and c.WAITER not in p.get("After", "").split(), "Old strict waiter still blocks T16")
            for key, value in self.plan["effective_controls"].get(name, {}).items():
                c.require(p.get(key) == value, "Effective option differs: " + name + ":" + key)
            if name.endswith(".service"):
                c.require(p.get("ProtectClock") == "yes", "ProtectClock differs: " + name)
            result[name] = p
        if direction == "rollback":
            c.require(self.m.show("system", NEW_WAITER)["LoadState"] == "not-found", "New waiter remains loaded")
        for path, op in self.plan["inner"]["operations"].items():
            if op[side]["kind"] == "file":
                c.require(c.metadata(self.fs.path(path)) == {"kind": "file", "uid": 0, "gid": 0, "mode": 0o644},
                          "Installed ownership differs: " + path)
        return result


class Handoff:
    def __init__(self, plan, approval, fs, managers, host, *, installer=lambda: None,
                 observation=lambda direction: None, requests=None, boot=c.boot,
                 sleep=time.sleep, monotonic=time.monotonic, fault=lambda point: None, request_review=None):
        validate(plan, approval)
        self.plan, self.approval, self.fs, self.m, self.host = plan, approval, fs, managers, host
        self.installer, self.observation = installer, observation
        self.requests = requests or (lambda: t.pending_requests(self.fs.path(REQUESTS)))
        self.boot, self.sleep, self.monotonic, self.fault = boot, sleep, monotonic, fault
        self.request_review = request_review
        self.directory = STATE + "/" + plan["plan_id"]
        inner_approval = {"purpose": t.PURPOSE, "plan_id": plan["inner"]["plan_id"], "enabled": True,
                          "approval_reference": self.directory + "/approval.json", "directions": ["apply", "rollback"]}
        self.tx = t.Transaction(plan["inner"], inner_approval, fs, host, boot=boot)

    def path(self, name):
        return self.directory + "/" + name

    def exists(self, name):
        return self.fs.node(name)["kind"] != "absent"

    def save(self):
        self.fs.save(self.path(self.direction + ".json"), self.state)

    def phase(self, name):
        self.state["phase"] = name
        self.save()
        self.fault("phase:" + name)

    def need(self, name):
        return PHASES.index(self.state["phase"]) < PHASES.index(name)

    def check_nodes(self):
        for path, op in self.plan["inner"]["operations"].items():
            allowed = list(op.values())
            if path in {c.SYSTEM_ROOT + name for name in c.SERVICES}:
                allowed.append(MASK)
            c.require(self.fs.node(path) in allowed, "Unknown intermediate file: " + path)

    def request_inventory(self):
        inventory = self.requests()
        self.state["request_inventory"] = inventory
        self.save()
        self.fault("request-inventory")
        unresolved = {j: r for j, r in inventory.items() if r["requires_reconciliation"]}
        if unresolved:
            review = self.request_review or {}
            expected = {job: record["sha256"] for job, record in unresolved.items()}
            c.require(self.direction == "rollback" and review.get("plan_id") == self.plan["plan_id"]
                      and review.get("action") == "preserve-for-reviewed-rollback"
                      and review.get("requests") == expected and review.get("reason"),
                      "Unfinished wrapper request: exact preservation review required before rollback")
            # This authorizes neither completing the request nor reissuing its
            # fixed scope with legacy code. Its identity/pins/evidence stay intact.
            self.fs.save(self.path("rollback-request-review.json"), review, immutable=True)
            self.state["preserved_unfinished_requests"] = expected
            self.save()

    def prepare_pending(self):
        """Called with coordinator EX only after PID/job and request rechecks."""
        progress_path = self.tx.name("progress.json")
        state = self.fs.json(t.LEDGER)
        if not self.exists(progress_path):
            c.require(self.direction == "apply" and state == self.plan["inner"]["ledger_before"], "Initial ledger differs")
            c.require(all(self.fs.node(p) == op["before"] for p, op in self.plan["inner"]["operations"].items()), "Initial files differ")
            self.fs.save(self.tx.name("plan.json"), self.plan["inner"], immutable=True)
            self.fs.save(self.tx.name("approval.json"), self.tx.approval, immutable=True)
            progress = {"direction": "apply", "phase": "intent", "base_ledger": state}
            self.fs.save(progress_path, progress)
            self.fault("inner-intent")
        else:
            c.require(self.fs.json(self.tx.name("plan.json")) == self.plan["inner"]
                      and self.fs.json(self.tx.name("approval.json")) == self.tx.approval, "Stored inner authority differs")
            progress = self.fs.json(progress_path)
            if progress["direction"] != self.direction:
                c.require(self.direction == "rollback", "Reverted plan requires a new plan")
                receipt = self.tx.name("apply-receipt.json")
                applied = self.exists(receipt) and state == self.fs.json(receipt)["ledger_after"]
                c.require(applied or state in (progress["base_ledger"], self.tx.pending(progress)), "Rollback ledger differs")
                progress = {"direction": "rollback", "phase": "intent", "base_ledger": state if applied else progress["base_ledger"]}
                self.fs.save(progress_path, progress)
                self.fault("rollback-intent")
        c.require(state in (progress["base_ledger"], self.tx.pending(progress), self.tx.pending({**progress, "direction": "apply"})),
                  "Ledger changed outside this transition")
        self.fs.save(t.LEDGER, self.tx.pending(progress))
        self.fault("pending-before-mask")
        for name in c.SERVICES:
            self.fs.write(c.SYSTEM_ROOT + name, MASK)
            self.fault("mask:" + name)

    def hold_and_seal(self, deadline):
        # No service-file or ledger changes while an old body is finishing.
        while True:
            self.host.immutable()
            self.host.auxiliary_idle()
            states = self.m.states("system", (*c.SERVICES, *c.TIMERS))
            if all(v["classification"] not in ("running", "unknown") for v in states.values()):
                try:
                    with self.fs.lock():
                        current = self.m.states("system", (*c.SERVICES, *c.TIMERS))
                        if any(v["classification"] in ("running", "unknown") for v in current.values()):
                            raise c.Hold("Work changed during coordinator acquisition")
                        self.host.auxiliary_idle()
                        self.request_inventory()
                        ledger = self.fs.json(t.LEDGER)
                        already_pending = (ledger.get("schema") == 2
                                           and ledger.get("plan_id") == self.plan["inner"]["plan_id"]
                                           and ledger.get("direction") == self.direction)
                        # Preserve the cutoff once pending is durable: reload or
                        # a denied retry can already have removed its waiting job.
                        # Before pending, old work may still finish; recapture it.
                        if not already_pending or "before_seal" not in self.state:
                            self.state["before_seal"] = current
                        self.save()
                        self.prepare_pending()
                    return
                except (BlockingIOError, c.Hold) as exc:
                    # Only a busy lock/new work is retryable. Input/request/ledger
                    # failures are left for explicit review, never reset.
                    if str(exc) not in ("Coordinator busy; running work is not interrupted", "Work changed during coordinator acquisition"):
                        raise
            c.require(self.monotonic() < deadline, "Normal drain pending; no body was stopped")
            self.sleep(1)

    def cancel_under_barrier(self, deadline):
        for name, value in self.state["before_seal"].items():
            if name in c.SERVICES and value["classification"] == "pending":
                self.state["cancelled"].setdefault(name, value)
        self.save()
        self.fault("barrier-request-inventory")
        self.m.command("system", "daemon-reload")
        while True:
            states = self.m.states("system", (*c.SERVICES, *c.TIMERS))
            for name, value in states.items():
                if name in c.SERVICES:
                    c.require(value["properties"]["LoadState"] == "masked", "Service start barrier missing")
                if value["classification"] == "pending":
                    self.state["cancelled"].setdefault(name, value)
                    self.save()
                    self.fault("cancel-intent:" + name)
                    fresh = self.m.states("system", (name,))[name]
                    if fresh["classification"] == "pending":
                        c.require(name in c.SERVICES and fresh["properties"]["LoadState"] == "masked", "Not a masked no-PID request")
                        self.m.command("system", "stop", "--no-block", name)
                        self.fault("cancel-return:" + name)
            if all(v["classification"] == "idle" for v in states.values()):
                break
            c.require(self.monotonic() < deadline, "Barrier drain pending; running bodies are never stopped")
            self.sleep(1)
        # The old waiter is shared by strict paths and is never a mutation target.
        if self.m.show("system", NEW_WAITER).get("LoadState") != "not-found":
            self.m.command("system", "stop", "--no-block", NEW_WAITER)
            while c.classify(self.m.show("system", NEW_WAITER), self.m.jobs("system")) != "idle":
                c.require(self.monotonic() < deadline, "New waiter cancellation pending")
                self.sleep(1)

    def unmask_for_transaction(self):
        with self.fs.lock():
            progress = self.fs.json(self.tx.name("progress.json"))
            c.require(self.fs.json(t.LEDGER) == self.tx.pending(progress), "Pending required before barrier removal")
            for name in c.SERVICES:
                path = c.SYSTEM_ROOT + name
                if self.fs.node(path) == MASK:
                    self.fs.write(path, self.plan["inner"]["operations"][path]["before" if self.direction == "apply" else "after"])
                self.fault("barrier-remove:" + name)
        # systemd still has the masks loaded until the complete stage is durable.

    def start_once(self, name):
        requests = self.state["starts"]
        current = self.m.show("system", name)
        previous = requests.get(name)
        if previous:
            if previous.get("accepted"):
                return
            changed = current.get("InvocationID") and current["InvocationID"] != previous["before"].get("InvocationID")
            entered = current.get("ActiveEnterTimestampMonotonic", "0") != previous["before"].get("ActiveEnterTimestampMonotonic", "0")
            c.require(changed or entered, "Ambiguous start; inspect request before reissuing: " + name)
            previous.update(accepted=True, reconciled=True, after=current)
            self.save()
            return
        requests[name] = {"before": current, "accepted": False}
        self.save()
        self.fault("start-intent:" + name)
        self.m.command("system", "start", "--no-block", name)
        self.fault("start-return:" + name)
        requests[name].update(accepted=True, after=self.m.show("system", name))
        self.save()

    def run(self, direction="apply"):
        c.require(direction in ("apply", "rollback") and self.boot() == self.plan["boot_id"], "Wrong direction/boot")
        self.host.immutable()
        self.check_nodes()
        self.direction = direction
        if direction == "apply":
            c.require(not self.exists(self.path("rollback.json")), "Rolled-back plan cannot be reapplied")
        self.fs.save(self.path("plan.json"), self.plan, immutable=True)
        self.fs.save(self.path("approval.json"), self.approval, immutable=True)
        if self.exists(self.path(direction + ".json")):
            self.state = self.fs.json(self.path(direction + ".json"))
        else:
            if direction == "rollback":
                c.require(self.exists(self.path("apply.json")), "No apply attempt to roll back")
            self.state = {"direction": direction, "phase": "new", "cancelled": {}, "starts": {}}
            self.save()
        if self.state.get("result", {}).get("early_rollback"):
            c.require(self.fs.json(t.LEDGER) == self.plan["inner"]["ledger_before"]
                      and all(self.fs.node(p) == op["before"] for p, op in self.plan["inner"]["operations"].items()),
                      "Early rollback drift")
            return self.state
        if not self.need("committed"):
            side = "after" if direction == "apply" else "before"
            receipt = self.fs.json(self.tx.name(direction + "-receipt.json"))
            c.require(self.fs.json(t.LEDGER) == receipt["ledger_after"], "Committed ledger changed")
            c.require(all(self.fs.node(p) == op[side] for p, op in self.plan["inner"]["operations"].items()), "Committed configuration drift")
        if self.need("installed"):
            if direction == "apply":
                self.state["installation"] = self.installer()
            self.phase("installed")
        if self.need("timers_held"):
            self.state.setdefault("timers_before_hold", self.m.states("system", c.TIMERS))
            self.save()
            for name in c.TIMERS:
                self.m.command("system", "stop", "--no-block", name)
                self.fault("timer-stop:" + name)
            self.phase("timers_held")
        deadline = self.monotonic() + self.plan["drain_seconds"]
        if direction == "rollback" and not self.exists(self.tx.name("progress.json")):
            # Pre-pending abort: no configuration or ledger was changed.
            c.require(self.fs.json(t.LEDGER) == self.plan["inner"]["ledger_before"], "Early rollback ledger differs")
            c.require(all(self.fs.node(p) == op["before"] for p, op in self.plan["inner"]["operations"].items()), "Early rollback files differ")
            for name in c.TIMERS:
                self.start_once(name)
            self.state["result"] = {"configuration": "unchanged", "early_rollback": True,
                                    "request_reconciliation_required": False}
            self.phase("complete")
            return self.state
        if self.need("sealed"):
            # If commit succeeded before the host phase receipt, finish that
            # direction instead of turning a closed successful ledger pending again.
            receipt = self.tx.name(direction + "-receipt.json")
            if self.exists(receipt) and self.fs.json(t.LEDGER) == self.fs.json(receipt)["ledger_after"]:
                self.state["commit"] = {"configuration": direction + "_committed", "recovered": True}
                self.phase("committed")
            else:
                self.hold_and_seal(deadline)
                self.phase("sealed")
        if self.need("drained"):
            self.cancel_under_barrier(deadline)
            self.phase("drained")
        if self.need("staged"):
            self.unmask_for_transaction()
            self.tx.stage(direction)
            self.fault("transaction-staged")
            self.m.command("system", "daemon-reload")
            self.phase("staged")
        if self.need("committed"):
            self.state["commit"] = self.tx.commit()
            self.phase("committed")
        if self.need("activated"):
            self.state["post_commit_clock"] = self.observation(direction)
            self.save()
            # A recorded no-PID request cancelled by this handoff is delivered
            # once, before timer catch-up can race its explicit delivery.
            for name in sorted(self.state["cancelled"]):
                self.start_once(name)
            # Timer catch-up requests are recorded; no redundant manual full daily.
            for name in c.TIMERS:
                self.start_once(name)
            # Cancelled requests and failed-before-activation starts require exact
            # reconciliation. Never replay them blindly after a rollback/timer start.
            pending = set()
            if direction == "rollback" and self.exists(self.path("apply.json")):
                applied = self.fs.json(self.path("apply.json"))
                displaced = set(applied["cancelled"]) | {n for n, v in applied.get("before_seal", {}).items()
                                                         if n in c.SERVICES and v["classification"] == "pending"}
                pending.update(n for n in displaced if not applied["starts"].get(n, {}).get("accepted"))
                pending.update(n for n, v in applied["starts"].items() if n in c.SERVICES and not v.get("accepted"))
            self.state["unreconciled_requests"] = sorted(pending)
            self.phase("activated")
        self.host.immutable()
        self.state["result"] = {"configuration": direction + "_committed", "clock_control": False,
            "checkpoint_deletion": False, "automatic_retirement": "masked", "operating_observation_pending": True,
            "request_reconciliation_required": bool(self.state.get("unreconciled_requests")
                                                    or self.state.get("preserved_unfinished_requests"))}
        self.phase("complete")
        return self.state


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--action", choices=("inspect", "apply", "rollback"), default="inspect")
    p.add_argument("--approve-plan", help="Exact reviewed plan ID; sudo execution records explicit authority")
    p.add_argument("--request-review", type=Path, help="Optional exact unresolved-request preservation review for rollback only")
    p.add_argument("--approve-review-hash", help="Explicit SHA256 of the reviewed rollback request record")
    args = p.parse_args()
    from deployment import load_checked, execute
    plan = load_checked()
    if args.action == "inspect":
        print(json.dumps({"plan_id": plan["plan_id"], "tool_id": plan["tool_id"],
            "preflight_passed": True, "operating_approval": False, "configuration_paths": 13}, indent=2))
    else:
        execute(plan, args)
