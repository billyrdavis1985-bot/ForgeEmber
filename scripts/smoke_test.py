"""One minimal live Muse Ember text call, through the budgeted client and the ledger.

Billy runs this by hand after review. CI never runs it, and the client refuses live calls in CI.

    read -s MODEL_API_KEY && export MODEL_API_KEY
    uv run python scripts/smoke_test.py --live --stage dev

Without --live it prints the plan and the worst-case cost, then exits without any network.

What it checks:
  * auth, endpoint and model id work (POST https://api.meta.ai/v1/responses, muse-ember-1.0)
  * the ledger reserves before and settles after, and the call lands in traces/calls.jsonl
  * the usage shape: input_tokens_details.cached_tokens and output_tokens_details.reasoning_tokens,
    and whether reasoning_tokens <= output_tokens (the docs say reasoning is a subset of output)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

from forgeember.budget.ledger import BudgetExceeded, Ledger
from forgeember.budget.pricing import fmt_usd, worst_case_cost
from forgeember.config import EMBER_MODEL, Paths, find_root
from forgeember.ember.client import (
    EmberAPIError,
    EmberClient,
    LiveCallsDisabled,
    TransportError,
    estimate_text_input_tokens,
)

INSTRUCTIONS = "You are a health check. Reply with exactly one lowercase word."
INPUT = "Reply with the word: ok"
PROMPT_VERSION = "smoke-v1"
MAX_OUTPUT_TOKENS = 64  # reasoning shares this budget; "minimal" effort keeps it small
EFFORT = "minimal"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--live", action="store_true", help="actually call the Meta Model API")
    parser.add_argument("--stage", required=True, choices=["dev"], help="budget stage (dev only)")
    args = parser.parse_args(argv)

    est = estimate_text_input_tokens([INSTRUCTIONS, INPUT])
    worst = worst_case_cost(
        EMBER_MODEL, input_tokens_upper_bound=est, max_output_tokens=MAX_OUTPUT_TOKENS
    )
    print(
        f"model={EMBER_MODEL} effort={EFFORT} "
        f"max_output_tokens={MAX_OUTPUT_TOKENS} stage={args.stage}"
    )
    print(f"input-token upper bound={est}  worst-case reservation={fmt_usd(worst)}")
    if not args.live:
        print("dry run: pass --live to make the call")
        return 2

    paths = Paths(root=find_root())
    prompt_sha = hashlib.sha256(f"{INSTRUCTIONS}\n{INPUT}".encode()).hexdigest()
    with Ledger(db_path=paths.ledger_db, trace_path=paths.calls_trace) as ledger:
        try:
            client = EmberClient.live(ledger, args.stage, live=True)
            result = client.respond(
                instructions=INSTRUCTIONS,
                input=INPUT,
                agent_role="smoke",
                input_kind="text",
                reasoning_effort=EFFORT,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                prompt_version=PROMPT_VERSION,
                prompt_sha256=prompt_sha,
            )
        except LiveCallsDisabled as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        except BudgetExceeded as exc:
            print(f"budget refused the call: {exc}", file=sys.stderr)
            return 1
        except (EmberAPIError, TransportError) as exc:
            print(f"call failed (full reservation kept as spent): {exc}", file=sys.stderr)
            return 1

    rec = result.record
    print(f"status={result.status} outcome={rec.outcome} response_id={result.response_id}")
    print(f"text={result.text!r}")
    usage = result.payload.get("usage", {})
    print("raw usage (token counts only):")
    print(json.dumps(usage, indent=2, sort_keys=True))
    print(
        f"cost={fmt_usd(rec.cost_nusd or 0)} reserved={fmt_usd(rec.reservation_nusd)} "
        f"latency={rec.latency_ms}ms"
    )
    if result.usage is not None:
        u = result.usage
        if u.reasoning_tokens <= u.output_tokens:
            print("check: reasoning_tokens <= output_tokens (consistent with 'subset of output')")
        else:
            print(
                "WARNING: reasoning_tokens > output_tokens. Tell Claude; "
                "the ledger already bills the larger."
            )
    print("next: uv run forgeember budget  &&  uv run python scripts/render_budget_readme.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
