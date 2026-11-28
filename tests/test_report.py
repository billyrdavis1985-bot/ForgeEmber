from decimal import Decimal

from conftest import req
from forgeember.budget.ledger import DEFAULT_CONFIG, Outcome, Usage
from forgeember.budget.report import render_text, snapshot, summarize


def summary_of(ledger):
    return summarize(
        ledger.calls(),
        config=ledger.config,
        effective_limits=ledger.effective_stage_limits(),
        reconciliations=ledger.reconciliations(),
    )


def test_empty_report(ledger):
    s = summary_of(ledger)
    lines = {ln.split("  ")[0]: " ".join(ln.split()) for ln in render_text(s).splitlines()}
    assert lines["Spent (settled)"] == "Spent (settled) $0.00 of $10.00 limit"
    assert lines["Headroom to hard cap"] == "Headroom to hard cap $9.00 (cap $9.00)"
    assert lines["Cost per item"] == "Cost per item n/a"
    assert s.headroom_to_cap_nusd == DEFAULT_CONFIG.global_cap_nusd
    assert s.projected_monthly_nusd() is None
    snap = snapshot(s)
    assert snap["spent_nusd"] == 0 and snap["cumulative"] == []


def test_metrics_from_calls(ledger):
    u1 = Usage(input_tokens=2000, cached_input_tokens=1500, output_tokens=400, reasoning_tokens=100)
    u2 = Usage(input_tokens=1000, cached_input_tokens=500, output_tokens=200, reasoning_tokens=50)
    ledger.settle(
        ledger.reserve(req(item_id="a", input_kind="email", agent_role="identity")),
        outcome=Outcome.OK,
        usage=u1,
    )
    ledger.settle(
        ledger.reserve(req(item_id="a", input_kind="email", agent_role="skeptic")),
        outcome=Outcome.OK,
        usage=u2,
    )
    ledger.settle(
        ledger.reserve(req(item_id="b", input_kind="pdf", agent_role="document")),
        outcome=Outcome.OK,
        usage=u2,
    )
    ledger.settle(
        ledger.reserve(req(item_id=None, input_kind="text", agent_role="smoke")),
        outcome=Outcome.OK,
        usage=u2,
    )
    open_r = ledger.reserve(req(stage="eval"))

    s = summary_of(ledger)
    c1 = 500 * 100 + 1500 * 4 + 400 * 500
    c2 = 500 * 100 + 500 * 4 + 200 * 500
    assert s.spent_nusd == c1 + 3 * c2
    assert s.open_nusd == open_r.reservation_nusd
    assert s.items == 2
    assert s.item_spend_nusd == c1 + 2 * c2  # the smoke call has no item_id
    assert s.cost_per_item_nusd == Decimal(c1 + 2 * c2) / 2
    assert s.projected_monthly_nusd() == round(Decimal(c1 + 2 * c2) / 2 * 3000)
    assert s.cache_hit_rate == Decimal(1500 + 3 * 500) / Decimal(2000 + 3 * 1000)
    assert s.cache_savings_nusd == (1500 + 3 * 500) * 96
    assert s.reasoning_share == Decimal(100 + 3 * 50) / Decimal(400 + 3 * 200)
    assert s.by_input_kind == {"email": c1 + c2, "pdf": c2, "text": c2}
    assert s.by_agent_role["identity"] == c1
    dev = next(st for st in s.stages if st.stage == "dev")
    assert dev.spent_nusd == s.spent_nusd
    assert [v for _, v in s.cumulative][-1] == s.spent_nusd
    text = render_text(s)
    assert "Cache hit rate" in text and "Reasoning-token share" in text


def test_reconciliation_shown(ledger):
    ledger.reconcile(meta_total_nusd=5)
    text = render_text(summary_of(ledger))
    assert "LEDGER UNDERCOUNTS META" in text
