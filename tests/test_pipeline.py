"""End-to-end slice: item -> Ember (ideal fake) -> verify -> policy -> holds + receipt."""

import json
from pathlib import Path

import pytest

from conftest import FakeTransport, ok_response
from forgeember import envelope
from forgeember.actions import HoldQueue, carry_out, write_receipt
from forgeember.agents.base import static_estimator
from forgeember.baseline import BaselineStore
from forgeember.budget.ledger import BudgetConfig, read_trace
from forgeember.budget.pricing import usd_to_nusd
from forgeember.ember.client import EmberClient, TransportResponse
from forgeember.pipeline import Screener, SkepticMode
from forgeember.policy import Risk, Tier
from ideal_ember import IdealEmber

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())["items"]
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")


def screen_with(transport, ledger, env, **kw):
    client = EmberClient(ledger=ledger, stage="eval", transport=transport)
    return Screener(client=client, baselines=BASELINES, **kw).screen(env)


@pytest.mark.parametrize("item", MANIFEST, ids=[i["id"] for i in MANIFEST])
def test_fixture_reaches_its_labeled_outcome(item, ledger):
    env = envelope.from_path(FIXTURES / item["file"])
    ember = IdealEmber(item, env)
    result = screen_with(ember, ledger, env)
    exp = item["expected"]
    assert result.errors == []
    assert result.facts.category == exp["category"]
    assert result.decision.risk.value == exp["risk"], result.decision.reasons
    assert sorted(a.kind for a in result.decision.held) == sorted(exp["held"])
    for act in result.decision.actions:  # T2 is never executed, T0 never held
        assert act.disposition == {Tier.T0: "execute", Tier.T1: "notify", Tier.T2: "hold"}[act.tier]
    if "fields" in exp:
        ext = result.extraction
        assert (ext.vendor_name, ext.invoice_number, ext.total) == (
            exp["fields"]["vendor_name"],
            exp["fields"]["invoice_number"],
            exp["fields"]["total"],
        )
    # every model call is in the ledger, and media calls were counted first
    generations = [c for c in ember.calls if c[0] == "/responses"]
    assert len(result.calls) == len(generations) == len(read_trace(ledger.trace_path))
    assert all(c.item_id == env.id for c in result.calls)
    assert {c.prompt_version.split(".")[0] for c in result.calls} >= {"triage"}


def test_routine_text_email_costs_one_call(ledger):
    item = next(i for i in MANIFEST if i["id"] == "em-003")
    env = envelope.from_path(FIXTURES / item["file"])
    ember = IdealEmber(item, env)
    result = screen_with(ember, ledger, env)
    assert [c.agent_role for c in result.calls] == ["coordinator"]
    assert [p for p, _ in ember.calls] == ["/responses"]  # text-only: no token-count call
    assert result.calls[0].reasoning_effort == "minimal"


def test_escalated_item_runs_risk_at_higher_effort_and_skeptic(ledger):
    item = next(i for i in MANIFEST if i["id"] == "em-001")
    env = envelope.from_path(FIXTURES / item["file"])
    result = screen_with(IdealEmber(item, env), ledger, env)
    roles = {c.agent_role: c.reasoning_effort for c in result.calls}
    assert roles == {"coordinator": "minimal", "risk": "medium", "skeptic": "medium"}


def test_skeptic_off_skips_it(ledger):
    item = next(i for i in MANIFEST if i["id"] == "em-001")
    env = envelope.from_path(FIXTURES / item["file"])
    result = screen_with(IdealEmber(item, env), ledger, env, skeptic_mode=SkepticMode.OFF)
    assert "skeptic" not in {c.agent_role for c in result.calls}
    assert result.decision.risk is Risk.HIGH  # code facts alone carry it


def test_code_facts_beat_a_benign_model(ledger):
    """Even if Ember says the bank-change invoice is fine, code's bank baseline holds it HIGH."""
    item = next(i for i in MANIFEST if i["id"] == "inv-002")
    env = envelope.from_path(FIXTURES / item["file"])

    class NaiveEmber(IdealEmber):
        def triage_result(self, body):
            return {**super().triage_result(body), "risk_signals": [], "escalate": False}

        def risk_assessment(self, body):
            return {"verdict": "benign", "signals": [], "rationale": "looks fine"}

    result = screen_with(NaiveEmber({**item, "attack": None}, env), ledger, env)
    assert result.decision.risk is Risk.HIGH
    assert (
        "bank account differs from every account on file for this vendor" in result.decision.reasons
    )


def test_hallucinated_iban_is_caught_by_verification(ledger):
    item = next(i for i in MANIFEST if i["id"] == "inv-001")
    env = envelope.from_path(FIXTURES / item["file"])

    class HallucinatingEmber(IdealEmber):
        def invoice_extraction(self, body):
            out = super().invoice_extraction(body)
            out["bank"] = {**out["bank"], "iban": "GB82 WEST 1234 5698 7654 32"}
            return out

    result = screen_with(HallucinatingEmber(item, env), ledger, env)
    assert result.decision.risk is Risk.HIGH
    assert any(c.name == "invoice.in_source:iban" and c.status == "fail" for c in result.checks)


def test_budget_stop_mid_item_still_decides_conservatively(make_ledger):
    # Room for the triage call only: the document call is refused.
    config = BudgetConfig(
        grant_nusd=usd_to_nusd("10"),
        global_cap_nusd=usd_to_nusd("0.0025"),
        stage_limits_nusd={"eval": usd_to_nusd("0.0025")},
    )
    ledger = make_ledger(config)
    item = next(i for i in MANIFEST if i["id"] == "inv-001")
    env = envelope.from_path(FIXTURES / item["file"])
    client = EmberClient(ledger=ledger, stage="eval", transport=IdealEmber(item, env))
    result = Screener(client=client, baselines=BASELINES, estimator=static_estimator(1500)).screen(
        env
    )
    assert result.budget_stopped
    assert [c.agent_role for c in result.calls] == ["coordinator"]
    assert result.decision.risk is Risk.MEDIUM
    assert [a.kind for a in result.decision.held] == ["pay"]


def test_invalid_model_output_marks_screening_incomplete(ledger):
    env = envelope.from_path(FIXTURES / "emails/em-004-newsletter.eml")
    transport = FakeTransport(script=[ok_response(text="not json at all")])
    result = screen_with(transport, ledger, env)
    assert result.triage is None
    assert any("invalid output" in e for e in result.errors)
    assert result.decision.risk is Risk.MEDIUM
    assert result.calls[0].cost_nusd > 0  # the failed call is still paid for


def test_api_error_marks_screening_incomplete(ledger):
    env = envelope.from_path(FIXTURES / "emails/em-004-newsletter.eml")
    transport = FakeTransport(script=[TransportResponse(503, {"error": {"type": "server_error"}})])
    result = screen_with(transport, ledger, env)
    assert result.facts.screening_complete is False
    assert [c.outcome for c in ledger.calls()] == ["failed"]


def test_holds_receipts_and_notifications(tmp_path, ledger):
    item = next(i for i in MANIFEST if i["id"] == "inv-002")
    env = envelope.from_path(FIXTURES / item["file"])
    result = screen_with(IdealEmber(item, env), ledger, env)
    holds = HoldQueue(tmp_path / "holds.sqlite")
    try:
        outcome = carry_out(
            result.decision,
            item_id=env.id,
            holds=holds,
            notifications=tmp_path / "notifications.jsonl",
        )
        assert len(outcome.held) == 2
        pending = holds.list()
        assert {h.action for h in pending} == {"pay", "change_bank_details"}
        assert all(h.risk == "high" for h in pending)
        # re-screening the same item updates the pending hold instead of duplicating it
        carry_out(result.decision, item_id=env.id, holds=holds, notifications=tmp_path / "n.jsonl")
        assert len(holds.list()) == 2
        with pytest.raises(ValueError):
            holds.decide(pending[0].hold_id, approve=True, decided_by=" ")
        decided = holds.decide(pending[0].hold_id, approve=False, decided_by="Billy Davis")
        assert decided.status == "rejected"
        with pytest.raises(ValueError):
            holds.decide(pending[0].hold_id, approve=True, decided_by="Billy Davis")
    finally:
        holds.close()
    path = write_receipt(result.receipt(outcome), tmp_path / "receipts")
    receipt = json.loads(path.read_text())
    assert receipt["item_id"] == env.id and receipt["risk"] == "high"
    assert receipt["cost_nusd"] == sum(c["cost_nusd"] for c in receipt["calls"]) > 0
    assert (tmp_path / "notifications.jsonl").read_text().count(env.id) == 1
