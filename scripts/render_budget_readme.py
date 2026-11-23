"""Regenerate the README "Budget & Trace" block, the spend chart and the snapshot.

    uv run python scripts/render_budget_readme.py          # write
    uv run python scripts/render_budget_readme.py --check  # exit 1 if anything is stale

Numbers come from the local ledger (var/ledger.sqlite). They are cross-checked against the
committed trace (traces/calls.jsonl) first. A fresh clone with an empty ledger can't
overwrite real numbers with zeros.
"""

from __future__ import annotations

import argparse
import sys

from forgeember.budget.ledger import Ledger
from forgeember.budget.publish import (
    PublishError,
    publish,
    snapshot_from_ledger,
    snapshot_from_traces,
)
from forgeember.config import Paths, find_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if outputs are stale")
    args = parser.parse_args(argv)
    paths = Paths(root=find_root())

    with Ledger(db_path=paths.ledger_db, trace_path=paths.calls_trace) as ledger:
        try:
            snap = snapshot_from_ledger(ledger)
        except PublishError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if snap != snapshot_from_traces(paths):
        print(
            "error: the local ledger and the committed trace disagree. Run this on the machine "
            "that holds var/ledger.sqlite, and don't hand-edit traces/.",
            file=sys.stderr,
        )
        return 1

    result = publish(paths, snap, check=args.check)
    for p in result.changed:
        verb = "stale" if args.check else "wrote"
        print(f"{verb}: {p.relative_to(paths.root)}")
    if args.check and result.changed:
        return 1
    if not result.changed:
        print("budget block, chart and snapshot are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
