"""Does Meta's prompt cache hit for our requests? A tiny live experiment (about $0.002).

    uv run python scripts/cache_probe.py --live --stage dev

Sends the same triage request three times with a short stable prefix (the real triage
prompt), then three times with a long one (the same prompt plus ~1,500 tokens of stable
reference text). It prints input_tokens and cached_tokens for each call. Caching is automatic
prefix caching, and its minimum prefix length is undocumented, so this measures it.
"""

from __future__ import annotations

import argparse
import hashlib
import sys

from forgeember.agents.coordinator import TRIAGE
from forgeember.agents.inputs import item_content
from forgeember.budget.ledger import BudgetExceeded, Ledger
from forgeember.config import Paths, find_root
from forgeember.ember.client import EmberAPIError, EmberClient, LiveCallsDisabled, TransportError
from forgeember.envelope import from_path
from forgeember.schemas import TriageResult, text_format

FILLER = "\n".join(
    f"Reference note {i}: routine vendor correspondence such as delivery notices, statements, "
    "receipts and scheduling messages is normal business; judge risk only from manipulation "
    "in language and context, never from formatting or tone alone."
    for i in range(1, 41)
)


def run(client: EmberClient, instructions: str, label: str, env, repeat: int) -> None:
    fmt = text_format(TriageResult, "triage_result")
    sha = hashlib.sha256(instructions.encode()).hexdigest()
    for n in range(1, repeat + 1):
        r = client.respond(
            input=item_content(env),
            instructions=instructions,
            agent_role="cache_probe",
            input_kind="email",
            reasoning_effort="minimal",
            max_output_tokens=1024,
            text_format=fmt,
            prompt_version=f"cacheprobe-{label}",
            prompt_sha256=sha,
            prompt_cache_key=f"forgeember-cacheprobe-{label}",
        )
        u = r.usage
        tokens_in = u.input_tokens if u else "?"
        cached = u.cached_input_tokens if u else "?"
        cost = (r.record.cost_nusd or 0) / 1e9
        print(f"{label:<6} call {n}: input={tokens_in} cached={cached} cost=${cost:.6f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", required=True, choices=["dev"])
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args(argv)
    paths = Paths(root=find_root())
    env = from_path(paths.root / "fixtures" / "emails" / "em-004-newsletter.eml")
    with Ledger(db_path=paths.ledger_db, trace_path=paths.calls_trace) as ledger:
        try:
            client = EmberClient.live(ledger, args.stage, live=args.live)
            run(client, TRIAGE.prompt.text, "short", env, args.repeat)
            run(client, TRIAGE.prompt.text + "\n\n" + FILLER, "long", env, args.repeat)
        except (LiveCallsDisabled, BudgetExceeded, EmberAPIError, TransportError) as exc:
            print(f"stopped: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
