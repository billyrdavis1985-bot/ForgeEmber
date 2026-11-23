"""Write the hackathon proof-of-usage files from the ledger.

    uv run python scripts/export_trace.py

Outputs traces/submission-trace.jsonl (one line per Ember call, with tokens, cost, prompt hash
and response id) and traces/summary.json (totals, stage split, reconciliations). Neither
file can contain a secret: both pass the same credential-pattern check as the live trace.
"""

from __future__ import annotations

import sys

from forgeember.budget.ledger import Ledger
from forgeember.budget.publish import PublishError, export_submission
from forgeember.config import Paths, find_root


def main() -> int:
    paths = Paths(root=find_root())
    with Ledger(db_path=paths.ledger_db, trace_path=paths.calls_trace) as ledger:
        try:
            trace, summary = export_submission(ledger, paths)
        except PublishError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"wrote {trace.relative_to(paths.root)}")
    print(f"wrote {summary.relative_to(paths.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
