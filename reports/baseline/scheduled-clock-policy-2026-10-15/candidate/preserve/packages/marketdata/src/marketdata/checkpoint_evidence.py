"""Immutable completion witnesses and causal order, independent of deletion authority.

Objects are bundled into regular checkpoints. A bundle does not depend on a
live registry or on predecessor checkpoint directories. No clock control here.
"""

import fcntl
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import checkpoint_clock as clock
from .checkpoint_locks import plain_path
from .domain import DataError, json_bytes, sha256
from .fileio import sync_parent

SIDECAR = "checkpoint-evidence.json"
SCHEMA = 1
HEX = re.compile(r"[0-9a-f]{64}")


def require(ok, message):
    if not ok:
        raise DataError(message)


def mkdir(path):
    path = plain_path(path)
    if not path.exists():
        mkdir(path.parent)
        path.mkdir()
        sync_parent(path)


def immutable(path, value):
    """Publish complete bytes exclusively; a crashed temp is never a proof."""
    path = plain_path(path)
    raw = json_bytes(value)
    if path.exists():
        require(path.read_bytes() == raw, "Immutable evidence conflict")
        return
    mkdir(path.parent)
    temporary = path.parent / (".pending-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            require(path.read_bytes() == raw, "Concurrent evidence conflict")
        sync_parent(path)
    finally:
        temporary.unlink(missing_ok=True)
        sync_parent(temporary)


def identity(root, manifest):
    raw = plain_path(Path(root) / "backup.json").read_bytes()
    require(json.loads(raw) == json.loads(json_bytes(manifest)), "Completed manifest differs")
    return {
        "backup_id": manifest["backup_id"],
        "manifest_sha256": sha256(raw),
        "source_root": manifest["source_root"],
        "scope": manifest["scope"],
    }


def validate_identity(value, scope):
    require(
        set(value) == {"backup_id", "manifest_sha256", "source_root", "scope"},
        "Evidence identity fields",
    )
    require(
        all(HEX.fullmatch(value[k]) for k in ("backup_id", "manifest_sha256")),
        "Evidence identity hash",
    )
    require(value["source_root"] == scope["source_root"], "Evidence source root differs")
    require(
        value["scope"] == "catalog_and_all_referenced_raw_revisions_snapshots",
        "Evidence data scope differs",
    )


class Graph:
    def __init__(self, objects, scope):
        self.objects, self.scope = objects, scope
        self.checkpoints, self.witnesses, self.baselines = {}, {}, {}
        for digest, value in objects.items():
            require(
                HEX.fullmatch(digest) and sha256(json_bytes(value)) == digest,
                "Evidence object hash differs",
            )
            require(
                value.get("schema") == SCHEMA and value.get("scope") == scope,
                "Evidence scope/schema differs",
            )
            require(HEX.fullmatch(value.get("code_id", "")), "Evidence code identity missing")
            kind = value["kind"]
            if kind == "baseline":
                entries = value["entries"]
                require(
                    len({i["backup_id"] for i in entries}) == len(entries),
                    "Duplicate baseline identity",
                )
                for entry in entries:
                    validate_identity(entry, scope)
                self.baselines[digest] = value
            elif kind in {"checkpoint", "posthoc"}:
                validate_identity(value["identity"], scope)
                bid = value["identity"]["backup_id"]
                target = self.checkpoints if kind == "checkpoint" else self.witnesses
                require(bid not in target, "Conflicting checkpoint/witness evidence")
                target[bid] = digest
                if value["observation"] is not None:
                    clock.validate_observation(value["observation"])
                else:
                    require(
                        kind == "checkpoint" and bool(value.get("clock_error")),
                        "Unexplained missing clock evidence",
                    )
            else:
                raise DataError("Unknown evidence object kind")
        require(len(self.baselines) <= 1, "Conflicting evidence baselines")
        identities = {}
        for value in objects.values():
            items = value["entries"] if value["kind"] == "baseline" else [value["identity"]]
            for ident in items:
                bid = ident["backup_id"]
                require(
                    bid not in identities or identities[bid] == ident,
                    "Conflicting manifest identity across proofs",
                )
                identities[bid] = ident
        for bid, digest in self.checkpoints.items():
            value = objects[digest]
            require(value["mode"] in {"posthoc", "after_completion"}, "Unknown completion mode")
            order = value["order"]
            if order is not None:
                require(order["baseline"] in self.baselines, "Missing order baseline")
                require(re.fullmatch(r"[0-9a-f]{32}", order["run_id"]), "Missing creation run")
                require(
                    value["mode"] == "after_completion",
                    "Posthoc witness cannot invent creation order",
                )
                require(
                    bid
                    not in {i["backup_id"] for i in self.baselines[order["baseline"]]["entries"]},
                    "Baseline copy reused as new",
                )
                parents = order["predecessors"]
                require(len(parents) == len(set(parents)), "Duplicate predecessor")
                for parent in parents:
                    require(
                        parent in objects and objects[parent]["kind"] == "checkpoint",
                        "Missing predecessor evidence",
                    )
                    require(objects[parent]["identity"]["backup_id"] != bid, "Self predecessor")
        for bid, digest in self.witnesses.items():
            value = objects[digest]
            parent = objects.get(value["checkpoint"])
            require(
                parent is not None and parent["kind"] == "checkpoint", "Missing posthoc predecessor"
            )
            require(
                parent["identity"] == value["identity"] and parent["observation"] is None,
                "Posthoc witness replaces valid evidence",
            )
        # Check the entire graph once, including relations not used for selection.
        self._ancestors = {}
        for bid in self.checkpoints:
            self.ancestors(bid, set())

    def ancestors(self, bid, visiting=None):
        if bid in self._ancestors:
            return self._ancestors[bid]
        visiting = set() if visiting is None else visiting
        require(bid not in visiting, "Cyclic checkpoint order")
        visiting.add(bid)
        value = self.objects[self.checkpoints[bid]]
        order, result = value["order"], set()
        if order:
            result.update(i["backup_id"] for i in self.baselines[order["baseline"]]["entries"])
            for digest in order["predecessors"]:
                parent = self.objects[digest]["identity"]["backup_id"]
                result.add(parent)
                result.update(self.ancestors(parent, visiting))
        visiting.remove(bid)
        self._ancestors[bid] = result
        return result

    def proof(self, ident):
        bid = ident["backup_id"]
        digest = self.witnesses.get(bid) or self.checkpoints.get(bid)
        if digest is None:
            return None
        value = self.objects[digest]
        require(value["identity"] == ident, "Completion manifest binding differs")
        if value["observation"] is None:
            return None
        return {
            "object_id": digest,
            "completion_upper_utc_ns": value["observation"]["upper_utc_ns"],
            "kind": value["kind"],
        }


def decode_bundle(raw, expected=None):
    result = json.loads(raw)
    require(result.get("schema") == SCHEMA, "Unsupported checkpoint evidence bundle")
    graph = Graph(result["objects"], result["scope"])
    if expected is not None:
        require(result["identity"] == expected, "Sidecar belongs to another checkpoint")
    value = result["objects"].get(result["completion"])
    require(
        value is not None
        and value["kind"] == "checkpoint"
        and value["identity"] == result["identity"],
        "Sidecar completion differs",
    )
    require(
        graph.checkpoints.get(result["identity"]["backup_id"]) == result["completion"],
        "Duplicate sidecar completion",
    )
    return {"bundle": result, "sha256": sha256(raw), "object_ids": sorted(result["objects"])}


def read_bundle(root, manifest=None):
    path = plain_path(Path(root) / SIDECAR)
    if not path.exists():
        return None
    return decode_bundle(
        path.read_bytes(), identity(root, manifest) if manifest is not None else None
    )


class Registry:
    def __init__(self, root, scope, code_id):
        self.root, self.scope, self.code_id = plain_path(root), scope, code_id

    @contextmanager
    def lock(self, *, create=False):
        if create:
            mkdir(self.root)
        require(self.root.is_dir(), "Evidence registry not installed")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def graph(self):
        objects = {}
        for path in sorted((self.root / "objects").glob("*.json")):
            raw = plain_path(path).read_bytes()
            require(sha256(raw) == path.stem, "Registry evidence hash differs")
            objects[path.stem] = json.loads(raw)
        return Graph(objects, self.scope)

    def put(self, value):
        value = {"schema": SCHEMA, "scope": self.scope, "code_id": self.code_id, **value}
        digest = sha256(json_bytes(value))
        immutable(self.root / "objects" / (digest + ".json"), value)
        return digest

    def baseline(self, identities):
        """Caller holds registry + maintenance/repository/input locks and verifies identities."""
        entries = sorted(identities, key=lambda i: i["backup_id"])
        graph = self.graph()
        if graph.baselines:
            digest, previous = next(iter(graph.baselines.items()))
            require(previous["entries"] == entries, "Existing baseline cannot be replaced")
            return digest
        require(not graph.objects, "Orphan registry requires review")
        for ident in entries:
            validate_identity(ident, self.scope)
        return self.put({"kind": "baseline", "entries": entries})

    def observe(self, ident, observer):
        """First valid post-completion witness only; never amend earlier bytes."""
        graph = self.graph()
        prior = graph.proof(ident)
        if prior:
            return prior["object_id"]
        observation = observer()
        clock.validate_observation(observation)
        bid = ident["backup_id"]
        if bid in graph.checkpoints:
            return self.put(
                {
                    "kind": "posthoc",
                    "identity": ident,
                    "checkpoint": graph.checkpoints[bid],
                    "observation": observation,
                }
            )
        return self.put(
            {
                "kind": "checkpoint",
                "identity": ident,
                "mode": "posthoc",
                "order": None,
                "observation": observation,
                "clock_error": None,
            }
        )


class Producer:
    def __init__(self, registry, observer):
        self.registry, self.observer = registry, observer

    def begin(self, source_root):
        graph = self.registry.graph()
        require(
            str(source_root) == self.registry.scope["source_root"], "Producer source root differs"
        )
        require(len(graph.baselines) == 1, "Evidence baseline not registered")
        superseded = set().union(*(graph.ancestors(bid) for bid in graph.checkpoints))
        frontier = set(graph.checkpoints) - superseded
        return {
            "baseline": next(iter(graph.baselines)),
            "predecessors": sorted(graph.checkpoints[bid] for bid in frontier),
            "run_id": uuid.uuid4().hex,
            "started_sample": clock.stamp(),
        }

    def complete(self, destination, manifest, order):
        ident = identity(destination, manifest)
        require(
            ident["backup_id"] not in self.registry.graph().checkpoints,
            "Duplicate creation identity",
        )
        observation, error = None, None
        try:
            observation = self.observer()
            clock.validate_observation(observation)
        except Exception as exc:
            observation, error = None, f"{type(exc).__name__}: {exc}"
        digest = self.registry.put(
            {
                "kind": "checkpoint",
                "mode": "after_completion",
                "identity": ident,
                "order": order,
                "observation": observation,
                "clock_error": error,
            }
        )
        graph = self.registry.graph()
        bundle = {
            "schema": SCHEMA,
            "scope": self.registry.scope,
            "identity": ident,
            "completion": digest,
            "objects": graph.objects,
        }
        immutable(Path(destination) / SIDECAR, bundle)
        return {
            "clock_evidence_state": "available" if observation else "missing",
            "clock_evidence_error": error,
            "evidence_sha256": sha256(json_bytes(bundle)),
            "completion_object": digest,
        }


def select(entries, graph, lower_utc_ns, *, keep_latest=3, min_age_hours=72):
    """Pure classification; graph must be restricted to verified archived evidence."""
    threshold = (min_age_hours * 3600 + clock.POLICY["retention_margin_seconds"]) * 10**9
    live = {e["backup_id"]: e for e in entries}
    require(len(live) == len(entries), "Duplicate live checkpoint")
    proofs, reasons, successors = {}, {}, {}
    for bid, entry in live.items():
        ident = {k: entry[k] for k in ("backup_id", "manifest_sha256", "source_root")}
        ident["scope"] = entry.get("scope", "catalog_and_all_referenced_raw_revisions_snapshots")
        proof = graph.proof(ident)
        proofs[bid] = proof
        reasons[bid] = []
        if proof is None:
            reasons[bid].append("completion_evidence_missing_or_unarchived")
        elif lower_utc_ns - proof["completion_upper_utc_ns"] < threshold:
            reasons[bid].append("completion_upper_bound_less_than_72h_plus_60s")
        successors[bid] = sorted(
            other for other in live if other in graph.checkpoints and bid in graph.ancestors(other)
        )
        if len(successors[bid]) < keep_latest:
            reasons[bid].append("fewer_than_three_proven_successors")
    retained = {bid for bid in live if reasons[bid]}
    # Protect ambiguous boundaries until every candidate has enough FINAL keeps.
    while True:
        added = {
            bid
            for bid in live
            if bid not in retained and len(set(successors[bid]) & retained) < keep_latest
        }
        if not added:
            break
        for bid in added:
            reasons[bid].append("fewer_than_three_retained_successors")
        retained.update(added)
    keep, candidates = [], []
    for bid in sorted(live):
        (keep if bid in retained else candidates).append(
            {
                **live[bid],
                "protection": reasons[bid],
                "age_seconds": None
                if proofs[bid] is None
                else (lower_utc_ns - proofs[bid]["completion_upper_utc_ns"]) / 1e9,
            }
        )
    return (
        keep,
        candidates,
        {
            bid: {
                "completion": proofs[bid],
                "retained_successors": sorted(set(successors[bid]) & retained),
            }
            for bid in live
        },
    )
