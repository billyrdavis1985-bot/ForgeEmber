"""End-to-end slice: item -> Ember (ideal fake) -> verify -> policy -> holds + receipt."""

import json
from pathlib import Path

import pytest

from conftest import FakeTransport, ok_response
from forgeember import envelope
from forgeember.actions import HoldQueue, carry_out, write_receipt
from forgeember.agents.base import counting_estimator, static_estimator
from forgeember.baseline import BaselineStore
from forgeember.budget.ledger import BudgetConfig, InputKind, read_trace
from forgeember.budget.pricing import usd_to_nusd
from forgeember.ember.client import EmberClient, TransportResponse
from forgeember.evaluate import _field_equal, _got_fields
from forgeember.pipeline import Screener, SkepticMode, code_findings
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
    got = _got_fields(result)
    for name, value in exp.get("fields", {}).items():
        assert _field_equal(name, value, got[name]), (name, value, got[name])
    # every model call is in the ledger: generations in the result, and the token
    # counts that bounded the media calls settled beside them in the trace
    generations = [c for c in ember.calls if c[0] == "/responses"]
    counts = [c for c in ember.calls if c[0] == "/responses/input_tokens"]
    assert len(result.calls) == len(generations)
    assert len(read_trace(ledger.trace_path)) == len(generations) + len(counts)
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


def test_escalated_item_runs_risk_and_skeptic_at_low_effort(ledger):
    item = next(i for i in MANIFEST if i["id"] == "em-001")
    env = envelope.from_path(FIXTURES / item["file"])
    result = screen_with(IdealEmber(item, env), ledger, env)
    roles = {c.agent_role: c.reasoning_effort for c in result.calls}
    assert roles == {"coordinator": "minimal", "risk": "low", "skeptic": "low"}


def test_skeptic_off_skips_it(ledger):
    item = next(i for i in MANIFEST if i["id"] == "em-001")
    env = envelope.from_path(FIXTURES / item["file"])
    result = screen_with(IdealEmber(item, env), ledger, env, skeptic_mode=SkepticMode.OFF)
    assert "skeptic" not in {c.agent_role for c in result.calls}
    assert result.decision.risk is Risk.HIGH  # code facts alone carry it


def test_benign_risk_does_not_erase_verified_triage_signals(ledger):
    """Triage quotes a real signal, risk says benign: the verdict floors at suspicious."""
    env = envelope.from_path(FIXTURES / "emails/em-004-newsletter.eml")
    quote = "three ways to cut oven energy costs"
    assert quote in (env.part("body").text or "")
    item = {
        "id": "em-004x",
        "scenario": "split verdict",
        "attack": None,
        "expected": {"category": "marketing"},
    }

    class SplitEmber(IdealEmber):
        def triage_result(self, body):
            out = super().triage_result(body)
            out["risk_signals"] = [
                {
                    "type": "urgency",
                    "part": "body",
                    "quote": quote,
                    "explanation": "triage saw urgency",
                }
            ]
            out["escalate"] = True
            return out

        def risk_assessment(self, body):
            return {"verdict": "benign", "signals": [], "rationale": "risk disagrees"}

    result = screen_with(SplitEmber(item, env), ledger, env)
    assert result.facts.ember_verdict == "suspicious"
    assert result.facts.verified_signals == 1
    assert result.decision.risk is Risk.MEDIUM


def test_prompt_facts_name_the_organization_and_executives():
    env = envelope.from_path(FIXTURES / "emails/em-003-maintenance-notice.eml")
    facts = code_findings(env, BASELINES).as_prompt_facts(BASELINES.organization)
    assert any("Ashgrove Bakery" in f for f in facts)
    assert any("Dana Whitlock" in f and "dana@ashgrovebakery.example" in f for f in facts)


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


def test_document_extraction_runs_despite_a_dismissive_triage(ledger):
    """Triage says normal mail with no extraction; code still extracts and holds pay."""
    item = next(i for i in MANIFEST if i["id"] == "inv-001")
    env = envelope.from_path(FIXTURES / item["file"])

    class DismissiveEmber(IdealEmber):
        def triage_result(self, body):
            return {
                "category": "normal_business",
                "summary": "nothing to see",
                "needs_document_extraction": False,
                "risk_signals": [],
                "escalate": False,
            }

    result = screen_with(DismissiveEmber(item, env), ledger, env)
    assert result.extraction is not None and result.extraction.is_invoice
    assert result.facts.category == "invoice"
    assert [a.kind for a in result.decision.held] == ["pay"]


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


def test_count_failure_falls_back_to_static_bound(ledger):
    """The count endpoint 500s: screening still completes, loudly, with exact books."""
    item = next(i for i in MANIFEST if i["id"] == "inv-001")
    env = envelope.from_path(FIXTURES / item["file"])

    class NoCounting(IdealEmber):
        def post_json(self, path, body):
            if path == "/responses/input_tokens":
                self.calls.append((path, body))
                return TransportResponse(500, {"error": {"type": "server_error"}})
            return super().post_json(path, body)

    result = screen_with(NoCounting(item, env), ledger, env, estimator=counting_estimator)
    assert result.extraction is not None and result.extraction.is_invoice
    assert any("static bound" in e for e in result.errors)
    assert result.decision.risk is Risk.MEDIUM  # completed via fallback, flagged as such
    assert [a.kind for a in result.decision.held] == ["pay"]
    outcomes = sorted(c.outcome for c in ledger.calls())
    assert outcomes == ["failed", "failed", "ok", "ok"]  # 2 counts + 2 generations


def test_count_refusal_stops_before_any_call(make_ledger):
    config = BudgetConfig(
        grant_nusd=usd_to_nusd("10"),
        global_cap_nusd=usd_to_nusd("0.001"),
        stage_limits_nusd={"eval": usd_to_nusd("0.001")},
    )
    ledger = make_ledger(config)
    item = next(i for i in MANIFEST if i["id"] == "inv-001")
    env = envelope.from_path(FIXTURES / item["file"])
    ember = IdealEmber(item, env)
    result = screen_with(ember, ledger, env)
    assert result.budget_stopped
    assert ember.calls == []  # the count's reservation was refused: nothing sent
    assert result.decision.risk is Risk.MEDIUM
    assert [a.kind for a in result.decision.held] == ["pay"]


def test_static_estimator_rejects_malformed_blocks():
    est = static_estimator(1000)
    with pytest.raises(ValueError, match="mappings"):
        est(
            None,
            input=[{"content": ["not-a-block"]}],
            instructions="i",
            fmt={},
            role="r",
            item_id=None,
            input_kind="pdf",
        )


def test_invalid_model_output_marks_screening_incomplete(ledger):
    env = envelope.from_path(FIXTURES / "emails/em-004-newsletter.eml")
    transport = FakeTransport(script=[ok_response(text="not json at all")])
    result = screen_with(transport, ledger, env)
    assert result.triage is None
    assert any("invalid output" in e for e in result.errors)
    assert result.decision.risk is Risk.MEDIUM
    assert result.calls[0].cost_nusd > 0  # the failed call is still paid for


def test_unverifiable_media_is_medium_at_minimum(ledger):
    """A video nobody could watch (the video call failed) is MEDIUM: code verified nothing."""

    class BlindToVideo(IdealEmber):
        def video_note(self, body):
            raise RuntimeError("unused")

        def post_json(self, path, body):
            if path == "/responses" and body["text"]["format"]["name"] == "video_note":
                from forgeember.ember.client import TransportResponse

                return TransportResponse(400, {"error": {"type": "invalid_request_error"}})
            return super().post_json(path, body)

    env = envelope.from_bytes(
        b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, source="clip.mp4", filename="clip.mp4"
    )
    assert env.kind is InputKind.VIDEO
    item = {
        "id": "video-001",
        "scenario": "a video clip",
        "attack": None,
        "expected": {"category": "normal_business"},
    }
    result = screen_with(BlindToVideo(item, env), ledger, env)
    assert result.video is None
    assert result.decision.risk is Risk.MEDIUM
    assert any("cannot check" in r for r in result.decision.reasons)
    assert [a.kind for a in result.decision.actions] == ["file", "tag", "notify_operator"]
    # a clip Ember watched and found nothing in is routine
    watched = screen_with(IdealEmber(item, env), ledger, env)
    assert watched.video is not None and watched.decision.risk is Risk.LOW


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
