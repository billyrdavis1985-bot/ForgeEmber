"""Generate fictional fraud and normal-business fixtures with known answers (Phase 1).

Planned outputs in fixtures/: invoices (PDF and image), bank-detail-change emails,
CEO-impersonation emails and voicemails, and normal business mail. Each comes with a label
file holding the expected route, fields and risk tier. Everything is fictional and generated
by deterministic code from a seed, so we own it and nothing needs hand-labeling.
"""

from __future__ import annotations

import sys


def main() -> int:
    print("gen_fixtures: not implemented yet (Phase 1, see docs/PLAN.md)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
