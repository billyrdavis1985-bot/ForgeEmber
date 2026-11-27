"""Write the usage-trajectory export from the committed call trace.

    uv run python scripts/export_usage_trajectory.py

Reads traces/calls.jsonl (settled Ember calls, oldest first) and writes
traces/usage-trajectory.json: totals, per-day buckets, and a per-call cumulative
spend series. All money math runs on integer nano-USD; USD figures are rounded
for readability. Like the other exports, the output passes the credential-pattern
check, so it can never carry a secret.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime

from forgeember.budget.ledger import assert_no_secrets
from forgeember.config import find_root

SCHEMA = "forgeember.usage-trajectory.v1"


def _usd(nusd: int) -> float:
    return round(nusd / 1_000_000_000, 6)


def build(points: list[dict]) -> dict:
    """points: parsed trace lines, oldest first. Returns the trajectory document."""
    cum_nusd = cum_in = cum_out = 0
    series: list[dict] = []
    days: dict[str, dict] = {}
    by_stage: dict[str, dict] = {}
    by_outcome: dict[str, int] = {}
    for seq, p in enumerate(points, 1):
        cost = int(p.get("cost_nusd") or 0)
        in_tok = int(p.get("input_tokens") or 0)
        out_tok = int(p.get("output_tokens") or 0)
        cum_nusd += cost
        cum_in += in_tok
        cum_out += out_tok
        ts = str(p.get("ts") or "")
        series.append(
            {
                "seq": seq,
                "ts": ts,
                "cost_nusd": cost,
                "cumulative_cost_nusd": cum_nusd,
                "cumulative_cost_usd": _usd(cum_nusd),
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "stage": p.get("stage"),
                "agent_role": p.get("agent_role"),
                "outcome": p.get("outcome"),
            }
        )
        day = days.setdefault(
            ts[:10], {"calls": 0, "cost_nusd": 0, "input_tokens": 0, "output_tokens": 0}
        )
        day["calls"] += 1
        day["cost_nusd"] += cost
        day["input_tokens"] += in_tok
        day["output_tokens"] += out_tok
        stage = by_stage.setdefault(
            str(p.get("stage")), {"calls": 0, "cost_nusd": 0, "input_tokens": 0, "output_tokens": 0}
        )
        stage["calls"] += 1
        stage["cost_nusd"] += cost
        stage["input_tokens"] += in_tok
        stage["output_tokens"] += out_tok
        by_outcome[str(p.get("outcome"))] = by_outcome.get(str(p.get("outcome")), 0) + 1
    daily = [
        {"date": date, **bucket, "cost_usd": _usd(bucket["cost_nusd"])}
        for date, bucket in sorted(days.items())
    ]
    stages = {
        name: {**bucket, "cost_usd": _usd(bucket["cost_nusd"])}
        for name, bucket in sorted(by_stage.items())
    }
    return {
        "schema": SCHEMA,
        "model": points[0].get("model") if points else None,
        "calls": len(points),
        "totals": {
            "cost_nusd": cum_nusd,
            "cost_usd": _usd(cum_nusd),
            "input_tokens": cum_in,
            "output_tokens": cum_out,
        },
        "by_stage": stages,
        "by_outcome": dict(sorted(by_outcome.items())),
        "daily": daily,
        "series": series,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the usage trajectory as JSON.")
    parser.add_argument("--trace", type=str, default=None)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)
    root = find_root()
    trace_path = args.trace or str(root / "traces" / "calls.jsonl")
    out_path = args.out or str(root / "traces" / "usage-trajectory.json")
    with open(trace_path, encoding="utf-8") as f:
        raw = f.read()
    points = [json.loads(line) for line in raw.splitlines() if line.strip()]
    doc = build(points)
    doc["source"] = {
        "trace": "traces/calls.jsonl",
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    text = json.dumps(doc, indent=1) + "\n"
    assert_no_secrets(text)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {out_path} ({len(points)} calls, ${_usd(doc['totals']['cost_nusd']):.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
