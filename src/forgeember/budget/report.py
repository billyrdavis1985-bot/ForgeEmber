"""Budget summary: one pure function from ledger records to every number we publish.

The CLI report, the README "Budget & Trace" block, the SVG chart and the submission summary
all come from ``summarize``. That's what lets CI check the README against the ledger.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from forgeember.budget.ledger import BudgetConfig, CallRecord, Reconciliation, Stage
from forgeember.budget.pricing import cache_savings_nusd, fmt_usd, nusd_to_usd

SNAPSHOT_SCHEMA = "forgeember.budget-snapshot.v1"
# Projection basis for the README: a small business receiving ~3,000 emails a month.
MONTHLY_ITEMS_SMALL_BUSINESS = 3_000


@dataclass(frozen=True, slots=True)
class StageLine:
    stage: str
    limit_nusd: int
    spent_nusd: int
    open_nusd: int

    @property
    def remaining_nusd(self) -> int:
        return self.limit_nusd - self.spent_nusd - self.open_nusd


@dataclass(frozen=True, slots=True)
class BudgetSummary:
    grant_nusd: int
    cap_nusd: int
    spent_nusd: int
    open_nusd: int
    calls_settled: int
    calls_open: int
    overruns: int
    stages: tuple[StageLine, ...]
    by_input_kind: Mapping[str, int]
    by_agent_role: Mapping[str, int]
    by_outcome: Mapping[str, int]
    items: int
    item_spend_nusd: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_savings_nusd: int
    first_ts: str | None
    last_ts: str | None
    cumulative: tuple[tuple[str, int], ...]  # (settled_at, cumulative spent nUSD)
    last_reconciliation: Reconciliation | None

    @property
    def headroom_to_cap_nusd(self) -> int:
        return self.cap_nusd - self.spent_nusd - self.open_nusd

    @property
    def cost_per_item_nusd(self) -> Decimal | None:
        if self.items == 0:
            return None
        return Decimal(self.item_spend_nusd) / self.items

    @property
    def cache_hit_rate(self) -> Decimal | None:
        if self.input_tokens == 0:
            return None
        return Decimal(self.cached_input_tokens) / self.input_tokens

    @property
    def reasoning_share(self) -> Decimal | None:
        if self.output_tokens == 0:
            return None
        return Decimal(self.reasoning_tokens) / self.output_tokens

    def projected_monthly_nusd(
        self, items_per_month: int = MONTHLY_ITEMS_SMALL_BUSINESS
    ) -> int | None:
        per_item = self.cost_per_item_nusd
        if per_item is None:
            return None
        return int((per_item * items_per_month).to_integral_value(rounding=ROUND_HALF_UP))


def summarize(
    calls: Iterable[CallRecord],
    *,
    config: BudgetConfig,
    effective_limits: Mapping[str, int] | None = None,
    reconciliations: Iterable[Reconciliation] = (),
) -> BudgetSummary:
    calls = list(calls)
    limits = dict(effective_limits or config.stage_limits_nusd)
    settled = sorted(
        (c for c in calls if c.status == "settled"), key=lambda c: (c.settled_at or "", c.call_id)
    )
    open_calls = [c for c in calls if c.status == "open"]

    spent_by_stage: dict[str, int] = defaultdict(int)
    open_by_stage: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    by_role: dict[str, int] = defaultdict(int)
    by_outcome: dict[str, int] = defaultdict(int)
    item_ids: set[str] = set()
    item_spend = 0
    tokens = {"in": 0, "cached": 0, "out": 0, "reasoning": 0}
    savings = 0
    cumulative: list[tuple[str, int]] = []
    running = 0

    for c in settled:
        cost = c.cost_nusd or 0
        spent_by_stage[c.stage] += cost
        by_kind[c.input_kind] += cost
        by_role[c.agent_role] += cost
        by_outcome[c.outcome or "unknown"] += 1
        if c.item_id is not None:
            item_ids.add(c.item_id)
            item_spend += cost
        tokens["in"] += c.input_tokens or 0
        tokens["cached"] += c.cached_input_tokens or 0
        tokens["out"] += c.output_tokens or 0
        tokens["reasoning"] += c.reasoning_tokens or 0
        if c.input_tokens is not None and c.cached_input_tokens:
            savings += cache_savings_nusd(
                c.model, input_tokens=c.input_tokens, cached_input_tokens=c.cached_input_tokens
            )
        running += cost
        cumulative.append((c.settled_at or "", running))

    for c in open_calls:
        open_by_stage[c.stage] += c.reservation_nusd

    stages = tuple(
        StageLine(
            stage=s.value,
            limit_nusd=limits.get(s.value, 0),
            spent_nusd=spent_by_stage.get(s.value, 0),
            open_nusd=open_by_stage.get(s.value, 0),
        )
        for s in Stage
    )
    recs = list(reconciliations)
    return BudgetSummary(
        grant_nusd=config.grant_nusd,
        cap_nusd=config.global_cap_nusd,
        spent_nusd=running,
        open_nusd=sum(open_by_stage.values()),
        calls_settled=len(settled),
        calls_open=len(open_calls),
        overruns=sum(1 for c in settled if c.overrun),
        stages=stages,
        by_input_kind=dict(sorted(by_kind.items())),
        by_agent_role=dict(sorted(by_role.items())),
        by_outcome=dict(sorted(by_outcome.items())),
        items=len(item_ids),
        item_spend_nusd=item_spend,
        input_tokens=tokens["in"],
        cached_input_tokens=tokens["cached"],
        output_tokens=tokens["out"],
        reasoning_tokens=tokens["reasoning"],
        cache_savings_nusd=savings,
        first_ts=settled[0].settled_at if settled else None,
        last_ts=settled[-1].settled_at if settled else None,
        cumulative=tuple(cumulative),
        last_reconciliation=recs[-1] if recs else None,
    )


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _per_item(value: Decimal | None, scale: int = 1) -> str:
    if value is None:
        return "n/a"
    nusd = int((value * scale).to_integral_value(rounding=ROUND_HALF_UP))
    return fmt_usd(nusd, places=6 if scale == 1 else 4)


def render_text(s: BudgetSummary) -> str:
    """Plain-text report for ``forgeember budget``."""
    lines = [
        "ForgeEmber budget",
        "=================",
        f"Spent (settled)        {fmt_usd(s.spent_nusd):>12}   of {fmt_usd(s.grant_nusd)} grant",
        f"Open reservations      {fmt_usd(s.open_nusd):>12}",
        f"Headroom to hard cap   {fmt_usd(s.headroom_to_cap_nusd):>12}   "
        f"(cap {fmt_usd(s.cap_nusd)})",
        f"Calls                  {s.calls_settled:>12}   settled, {s.calls_open} open, "
        f"{s.overruns} overruns",
        "",
        f"By stage     {'limit':>12} {'spent':>12} {'open':>12} {'remaining':>12}",
    ]
    for st in s.stages:
        lines.append(
            f"  {st.stage:<10} {fmt_usd(st.limit_nusd):>12} {fmt_usd(st.spent_nusd):>12} "
            f"{fmt_usd(st.open_nusd):>12} {fmt_usd(st.remaining_nusd):>12}"
        )
    lines += ["", "By input kind"]
    lines += [f"  {k:<14} {fmt_usd(v):>12}" for k, v in s.by_input_kind.items()] or ["  (none)"]
    lines += ["", "By agent role"]
    lines += [f"  {k:<14} {fmt_usd(v):>12}" for k, v in s.by_agent_role.items()] or ["  (none)"]
    lines += [
        "",
        f"Items screened         {s.items:>12}",
        f"Cost per item          {_per_item(s.cost_per_item_nusd):>12}",
        f"Cost per 1,000 items   {_per_item(s.cost_per_item_nusd, 1000):>12}",
        f"Cache hit rate         {_pct(s.cache_hit_rate):>12}   "
        f"({s.cached_input_tokens:,} of {s.input_tokens:,} input tokens)",
        f"Cache savings          {fmt_usd(s.cache_savings_nusd):>12}",
        f"Reasoning-token share  {_pct(s.reasoning_share):>12}   "
        f"({s.reasoning_tokens:,} of {s.output_tokens:,} output tokens)",
    ]
    rec = s.last_reconciliation
    if rec is not None:
        status = "LEDGER UNDERCOUNTS META" if rec.undercounts else "ok (ledger >= Meta)"
        lines += [
            "",
            f"Last reconciliation    {rec.ts}: Meta {fmt_usd(rec.meta_total_nusd)} vs ledger "
            f"{fmt_usd(rec.ledger_total_nusd)} -> {status}",
        ]
    return "\n".join(lines) + "\n"


def snapshot(s: BudgetSummary) -> dict[str, Any]:
    """Deterministic, JSON-safe snapshot. Committed as traces/budget-snapshot.json."""
    rec = s.last_reconciliation
    projected = s.projected_monthly_nusd()
    per_item = s.cost_per_item_nusd
    return {
        "schema": SNAPSHOT_SCHEMA,
        "grant_nusd": s.grant_nusd,
        "cap_nusd": s.cap_nusd,
        "spent_nusd": s.spent_nusd,
        "open_nusd": s.open_nusd,
        "headroom_to_cap_nusd": s.headroom_to_cap_nusd,
        "calls_settled": s.calls_settled,
        "calls_open": s.calls_open,
        "overruns": s.overruns,
        "stages": [
            {
                "stage": st.stage,
                "limit_nusd": st.limit_nusd,
                "spent_nusd": st.spent_nusd,
                "open_nusd": st.open_nusd,
            }
            for st in s.stages
        ],
        "by_input_kind_nusd": dict(s.by_input_kind),
        "by_agent_role_nusd": dict(s.by_agent_role),
        "by_outcome_calls": dict(s.by_outcome),
        "items": s.items,
        "item_spend_nusd": s.item_spend_nusd,
        "cost_per_item_nusd": None
        if per_item is None
        else str(per_item.quantize(Decimal("0.001"))),
        "projected_monthly_nusd_3000_items": projected,
        "input_tokens": s.input_tokens,
        "cached_input_tokens": s.cached_input_tokens,
        "output_tokens": s.output_tokens,
        "reasoning_tokens": s.reasoning_tokens,
        "cache_savings_nusd": s.cache_savings_nusd,
        "first_ts": s.first_ts,
        "last_ts": s.last_ts,
        "cumulative": [[ts, v] for ts, v in s.cumulative],
        "last_reconciliation": None
        if rec is None
        else {
            "ts": rec.ts,
            "meta_total_nusd": rec.meta_total_nusd,
            "ledger_total_nusd": rec.ledger_total_nusd,
            "difference_nusd": rec.difference_nusd,
        },
    }


def usd(nusd: int) -> Decimal:
    return nusd_to_usd(nusd)
