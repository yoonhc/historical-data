"""Reuse the reviewed raw/replay failure auditor for both frozen partial captures."""

import importlib.util
import json
import os

from audit import OUT, ROOT, ReadOnlyStore, save
from marketdata.domain import now


def main():
    os.nice(10)
    path = OUT.parent / "depth-observation-2026-10-03/analyze_failures.py"
    spec = importlib.util.spec_from_file_location("prior_failure_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUT = OUT
    selection = json.loads((OUT / "audit.json").read_bytes())["partial_ids"]
    assert selection == ["c595e79851ef4ced8e4194f22cf58b84", "54d28ea721ed41fab80928c064514159"]
    results = {"started_at": now(), "selection": selection, "cases": []}
    try:
        for cid in selection:
            reader = ReadOnlyStore(ROOT)
            try:
                results["cases"].append(module.audit(reader, cid))
            finally:
                reader.close()
        results.update(complete=True, finished_at=now())
        save("failures-verification.json", results)
    except Exception as exc:
        save(
            "failures-audit-error.json",
            {"at": now(), "type": type(exc).__name__, "error": str(exc)},
        )
        raise


if __name__ == "__main__":
    main()
