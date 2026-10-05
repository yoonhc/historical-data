"""Run the existing canonical timing/hash/ID audit over the 45 frozen captures."""

import importlib.util

from audit import OUT, save


def main():
    path = OUT.parent / "depth-observation-2026-10-03/check_receipts.py"
    spec = importlib.util.spec_from_file_location("prior_receipt_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def save_current_scope(name, value):
        value["scope"] = (
            "All 45 frozen closed captures; canonical timing/ID columns and file hashes, "
            "not all raw price/quantity pairs; recv times are not wire arrivals; "
            "physical socket gaps are not repaired"
        )
        assert len(value["sessions"]) == 45
        save(name, value)

    module.save = save_current_scope
    module.main()


if __name__ == "__main__":
    main()
