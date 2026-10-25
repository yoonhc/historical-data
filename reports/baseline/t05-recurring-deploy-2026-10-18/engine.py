"""One fixed T05 configuration transaction; never invokes retirement/backup.

The host adapter supplies real manager inspection and narrow timer commands.
The same state machine/writer is used with a disposable root in the tests.
"""

import copy
from datetime import datetime, timezone

from vendor import handoff_common as c

AUTO = "marketdata-checkpoint-retirement"
SERVICE, TIMER = AUTO + ".service", AUTO + ".timer"
SYSTEM = "/etc/systemd/system/"
GRANT = "/etc/marketdata/checkpoint-clock-retirement/recurring.json"
MASK = {"kind": "symlink", "target": "/dev/null"}
ABSENT = {"kind": "absent"}


def file_node(raw):
    return {"kind": "file", "mode": 0o644, "text": raw.decode(), "sha256": c.sha(raw)}


def operation_id(plan):
    return c.sha(c.encode({k: v for k, v in plan.items() if k != "operation_plan_id"}))


class Engine:
    def __init__(self, plan, files, host, hook=lambda name: None):
        c.require(operation_id(plan) == plan["operation_plan_id"], "Plan digest differs")
        self.p, self.f, self.h, self.hook = plan, files, host, hook
        self.root = (
            "/var/lib/marketdata-clock/t05-recurring-operations/" + plan["operation_plan_id"]
        )
        self.path = self.root + "/state.json"
        self.s = self.f.json(self.path) if self.f.node(self.path)["kind"] != "absent" else None

    def save(self):
        self.f.save(self.path, self.s)

    def step(self, name, function):
        if name in self.s["done"]:
            return self.s["results"].get(name)
        self.s["intent"] = name
        self.save()
        self.hook(name + ":intent")
        result = function()
        self.hook(name + ":effect")
        self.s["results"][name] = result
        self.s["done"].append(name)
        self.s["intent"] = None
        self.save()
        self.hook(name + ":done")
        return result

    def authority(self):
        path = self.root + "/authority.json"
        if self.f.node(path)["kind"] == "absent":
            self.f.save(
                path,
                {
                    "operation_plan_id": self.p["operation_plan_id"],
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "approval_reference": path,
                    "meaning": "Explicit --approve-operation accepted; no claim of earlier approval",
                    "scope_id": self.p["disabled_proposal"]["scope_id"],
                },
                immutable=True,
            )
        proof = self.f.json(path)
        c.require(
            proof["operation_plan_id"] == self.p["operation_plan_id"]
            and proof["scope_id"] == self.p["disabled_proposal"]["scope_id"]
            and proof["approval_reference"] == path,
            "Approval receipt differs",
        )
        active = copy.deepcopy(self.p["disabled_proposal"])
        active.update(enabled=True, approved_at=proof["approved_at"], approval_reference=path)
        node = file_node(c.encode(active))
        self.f.save(self.root + "/active-grant.json", active, immutable=True)
        self.f.save(
            self.root + "/active-grant-hash.json", {"sha256": node["sha256"]}, immutable=True
        )
        return node

    def acceptable(self):
        active = self.authority_node
        for path, item in self.p["changes"].items():
            allowed = [item["before"], item["after"]]
            if path == GRANT:
                allowed.append(active)
            if path in (SYSTEM + SERVICE, SYSTEM + TIMER):
                allowed.append(MASK)
            c.require(self.f.node(path) in allowed, "Unreviewed configuration: " + path)
            self.h.permissions(path)

    def write(self, path, node):
        self.acceptable()
        if self.f.node(path) != node:
            self.f.write(path, node)
        c.require(self.f.node(path) == node, "Write verification failed: " + path)
        self.h.permissions(path)

    def check_target(self, *, enabled):
        for path, item in self.p["changes"].items():
            node = self.authority_node if path == GRANT and enabled else item["after"]
            if path == GRANT and not enabled:
                node = self.p["changes"][GRANT]["after"]
            if path == self.p["enable_link"] and not enabled:
                node = ABSENT
            c.require(self.f.node(path) == node, "Target configuration differs: " + path)
            self.h.permissions(path)

    def apply(self):
        c.require(self.s["direction"] == "apply", "Stopped/rolled-back operation cannot reactivate")
        if "apply_complete" in self.s["done"]:
            self.h.verify_release()
            self.check_target(enabled=True)
            self.h.verify_active()
            return self.result("already_activated", operating_observation_pending=True)
        self.step("install", self.h.install)
        self.h.verify_release()
        # No production configuration has changed before final-path import.
        self.step("import", self.h.import_check)
        if "timer_start" in self.s["done"] or self.s.get("intent") == "timer_start":
            # A crash after start can leave no completion receipt. Never issue
            # another start based on an uncertain reply. Inactive => review.
            self.check_target(enabled=True)
            self.h.verify_active()
            self.step("timer_start", lambda: self.h.verify_active())
        else:
            self.h.require_timer_idle()
            self.h.drain()
            with self.h.configuration_lock():
                self.h.invariants(activation=True)
                self.h.require_idle()
                self.h.unfinished()
                disabled = self.p["changes"][GRANT]["after"]
                self.step("disabled_grant", lambda: self.write(GRANT, disabled))
                for path in self.p["configuration_order"]:
                    self.step(
                        "configure:" + path,
                        lambda path=path: self.write(path, self.p["changes"][path]["after"]),
                    )
                self.step("reload", self.h.reload)
                # Rerun this read-only proof on every resumed activation, even
                # if a prior invocation recorded it. Scope is never adopted.
                observed = self.h.probe()
                c.require(
                    observed["proposal"] == self.p["disabled_proposal"], "Installed scope differs"
                )
                self.s["results"]["installed_probe"] = observed
                self.save()
                self.step("grant", lambda: self.write(GRANT, self.authority_node))
                link = self.p["enable_link"]
                self.step("enable_link", lambda: self.write(link, self.p["changes"][link]["after"]))
                self.step("enable_reload", self.h.reload)
                self.check_target(enabled=True)
                self.h.invariants(activation=True)
            # Release the runner lock before starting the timer. Calendar-only
            # activation has no immediate job, and runtime retains its guard.
            self.step("timer_start", self.h.start_timer)
        self.h.verify_active()
        self.step(
            "apply_complete", lambda: {"configuration": "activated", "observations": "pending"}
        )
        return self.result("activated", operating_observation_pending=True)

    def close(self, action):
        # Timer-only stop never stops an executing retirement service or its
        # dependencies. No coordinator/runner lock is held during drain.
        self.step("stop_timer", self.h.stop_timer)
        self.h.require_timer_idle()
        self.h.drain()
        with self.h.configuration_lock():
            self.h.require_idle()
            self.h.invariants(activation=False)
            current = self.f.node(GRANT)
            disabled = (
                self.p["changes"][GRANT]["after"] if current == self.authority_node else current
            )
            self.step("revoke", lambda: self.write(GRANT, disabled))
            for path in (SYSTEM + SERVICE, SYSTEM + TIMER):
                self.step("mask:" + path, lambda path=path: self.write(path, MASK))
            self.step("unlink_enable", lambda: self.write(self.p["enable_link"], ABSENT))
            if action == "rollback":
                for path, item in self.p["changes"].items():
                    self.step(
                        "restore:" + path,
                        lambda path=path, item=item: self.write(path, item["before"]),
                    )
            self.step(action + "_reload", self.h.reload)
            self.h.verify_closed()
            if action == "rollback":
                for path, item in self.p["changes"].items():
                    c.require(self.f.node(path) == item["before"], "Rollback differs")
            self.step(action + "_complete", lambda: {"new_records": "preserved"})
        return self.result(
            action + "_masked", unfinished_review=self.h.unfinished(review_only=True)
        )

    def run(self, action, approval):
        c.require(action in ("apply", "stop", "rollback"), "Invalid action")
        c.require(
            approval == self.p["operation_plan_id"], "Explicit exact operation approval required"
        )
        self.h.invariants(activation=action == "apply")
        if self.s is None:
            c.require(action == "apply", "No operation to stop/roll back")
            for path, item in self.p["changes"].items():
                c.require(
                    self.f.node(path) == item["before"], "Initial configuration differs: " + path
                )
                self.h.permissions(path)
            self.h.require_timer_idle()
            self.s = {
                "operation_plan_id": approval,
                "direction": "apply",
                "done": [],
                "results": {},
                "intent": None,
            }
            self.f.save(self.root + "/operation-plan.json", self.p, immutable=True)
            self.save()
        c.require(self.s["operation_plan_id"] == approval, "Operation state differs")
        self.authority_node = self.authority()
        self.acceptable()
        if action != "apply":
            c.require(
                self.s["direction"] != "rollback" or action == "rollback", "Rollback is terminal"
            )
            self.s["direction"] = action
            self.save()
        return self.apply() if action == "apply" else self.close(action)

    def result(self, phase, **extra):
        self.h.invariants(activation=False)
        return {
            "operation_plan_id": self.p["operation_plan_id"],
            "phase": phase,
            "passed": True,
            "checkpoint_deletion_requested": False,
            "clock_control": False,
            "user_automation": "masked",
            "active_grant_sha256": self.authority_node["sha256"],
            **extra,
        }
