"""Identity specialist: Ember calls read-only tools, code runs them and checks the answer."""

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import FakeTransport, function_call_payload, ok_response, responses_payload
from forgeember import envelope
from forgeember.agents.base import MAX_TOOL_ROUNDS, run_agent, run_tool_agent
from forgeember.agents.specialists import DOCUMENT, IDENTITY
from forgeember.baseline import BaselineStore
from forgeember.budget.ledger import InputKind, read_trace
from forgeember.ember.client import EmberClient, TransportResponse
from forgeember.pipeline import Screener, code_findings
from forgeember.policy import Risk
from forgeember.tools import VendorTools

ROOT = Path(__file__).resolve().parents[1]
BASELINES = BaselineStore.load(ROOT / "fixtures" / "baselines.json")
EM008 = ROOT / "fixtures" / "emails" / "em-008-vendor-name-other-domain.eml"
CLAIM = "This is the accounts team at Fennimore Stationery."


def _identity(**overrides: Any) -> dict[str, Any]:
    answer = {
        "claimed_sender": "Fennimore Stationery",
        "claim_part": "body",
        "claim_quote": CLAIM,
        "vendor_id": "fennimore",
        "consistent_with_records": False,
        "explanation": "Claims to be Fennimore but writes from another domain.",
    }
    return {**answer, **overrides}


class ToolEmber:
    """Triage routes a payment request; identity follows a script of tool rounds."""

    def __init__(self, identity_rounds: list[dict[str, Any]]):
        self.identity_rounds = list(identity_rounds)
        self.bodies: list[dict] = []

    def post_json(self, path, body):
        name = body["text"]["format"]["name"]
        if name == "identity_assessment":
            self.bodies.append(body)
            step = self.identity_rounds.pop(0)
            if "calls" in step:
                return TransportResponse(200, function_call_payload(step["calls"]))
            return TransportResponse(200, responses_payload(text=json.dumps(step)))
        answers = {
            "triage_result": {
                "category": "payment_request",
                "summary": "A payment demand.",
                "needs_document_extraction": False,
                "risk_signals": [],
                "escalate": False,
            },
            "risk_assessment": {"verdict": "benign", "signals": [], "rationale": "ok"},
            "skeptic_view": {
                "counter_verdict": "suspicious",
                "argument": "a",
                "evidence_that_would_change_my_mind": "b",
            },
        }
        return TransportResponse(200, responses_payload(text=json.dumps(answers[name])))


LOOKUP = {"calls": [("call_1", "lookup_vendor", {"query": "Fennimore Stationery"})]}


def _screen(ledger, ember, **kw):
    client = EmberClient(ledger=ledger, stage="eval", transport=ember)
    return Screener(client=client, baselines=BASELINES, **kw).screen(envelope.from_path(EM008))


def test_tool_round_trip_finds_a_vendor_claim_from_the_wrong_domain(ledger):
    ember = ToolEmber([LOOKUP, _identity()])
    result = _screen(ledger, ember)

    first, second = ember.bodies
    assert [t["name"] for t in first["tools"]] == ["lookup_vendor"]
    assert first["tools"][0]["type"] == "function"  # never a hosted tool
    replay = [i for i in second["input"] if i.get("type", "").startswith("function_call")]
    assert [i["type"] for i in replay] == ["function_call", "function_call_output"]
    assert {i["call_id"] for i in replay} == {"call_1"}
    looked_up = json.loads(replay[1]["output"])
    assert looked_up["vendor_id"] == "fennimore" and "iban" not in json.dumps(looked_up).lower()

    assert [c.name for c in result.tool_calls] == ["lookup_vendor"]
    statuses = {c.name: c.status.value for c in result.checks if c.name.startswith("identity.")}
    assert statuses == {
        "identity.claim": "pass",
        "identity.vendor_from_lookup": "pass",
        "identity.sender_domain": "fail",
    }
    assert result.facts.vendor_claim_mismatch
    assert result.decision.risk is Risk.HIGH
    assert [a.kind for a in result.decision.held] == ["pay"]
    # both identity rounds are in the receipt and the ledger, not just the last
    roles = [c.agent_role for c in result.calls]
    assert roles.count("identity") == 2
    assert [r.agent_role for r in read_trace(ledger.trace_path)].count("identity") == 2
    assert result.receipt()["tool_calls"][0]["output"]["found"] is True


def test_an_invented_vendor_id_is_not_trusted(ledger):
    """No lookup returned it, so code ignores it: no mismatch, and the check says why."""
    result = _screen(ledger, ToolEmber([_identity()]))  # answers without looking anything up

    checks = {c.name: c.status.value for c in result.checks}
    assert checks["identity.vendor_from_lookup"] == "fail"
    assert "identity.sender_domain" not in checks
    assert not result.facts.vendor_claim_mismatch


def test_a_claim_not_in_the_item_is_not_trusted(ledger):
    answer = _identity(claim_quote="Fennimore Stationery confirms this request.")
    result = _screen(ledger, ToolEmber([LOOKUP, answer]))

    checks = {c.name: c.status.value for c in result.checks}
    assert checks["identity.claim"] == "fail"
    assert not result.facts.vendor_claim_mismatch


def test_a_model_that_keeps_calling_tools_is_stopped(ledger):
    result = _screen(ledger, ToolEmber([LOOKUP] * MAX_TOOL_ROUNDS))

    assert any("still calling tools" in e for e in result.errors)
    assert [c.agent_role for c in result.calls].count("identity") == MAX_TOOL_ROUNDS
    assert result.decision.risk is not Risk.LOW  # screening incomplete: at least MEDIUM


def test_bad_tool_calls_get_error_results_not_crashes(ledger):
    many = [(f"c{i}", "lookup_vendor", {"query": "Fennimore"}) for i in range(6)]
    rounds = [
        {"calls": [("c_bad", "lookup_vendor", {"q": 1}), ("c_x", "delete_vendor", {"query": "a"})]},
        {"calls": many},
        _identity(),
    ]
    ember = ToolEmber(rounds)
    result = _screen(ledger, ember)

    outputs = {
        i["call_id"]: json.loads(i["output"])
        for i in ember.bodies[-1]["input"]
        if i.get("type") == "function_call_output"
    }
    assert "error" in outputs["c_bad"] and "unknown tool" in outputs["c_x"]["error"]
    assert outputs["c3"]["found"] is True and "at most 4" in outputs["c4"]["error"]
    assert result.facts.vendor_claim_mismatch  # the good lookups still count


def test_bulk_mode_and_known_senders_skip_identity(ledger):
    ember = ToolEmber([])
    result = _screen(ledger, ember, bulk=True)
    assert ember.bodies == [] and result.identity is None

    known = ROOT / "fixtures" / "emails" / "em-005-invoice-attached.eml"  # Fennimore's domain
    client = EmberClient(ledger=ledger, stage="eval", transport=ToolEmber([]))
    screener = Screener(client=client, baselines=BASELINES)
    env = envelope.from_path(known)
    assert not screener._needs_identity(env, "invoice", code_findings(env, BASELINES))


@pytest.mark.parametrize(
    ("query", "vendor_id"),
    [
        ("Fennimore Stationery", "fennimore"),
        ("fennimore-stationery.example", "fennimore"),
        ("@kestrelbyte.example", "kestrel"),
        ("Kestrel Byte", "kestrel"),
        ("the accounts team at Marrowvale Couriers Ltd", "marrowvale"),
        ("Fennimore", "fennimore"),  # whole words of one vendor's name
        ("Brightwater Supplies", None),
        ("Stat", None),  # not a whole word
        ("Ltd", None),  # too short to mean anything
    ],
)
def test_lookup_vendor(query, vendor_id):
    out = VendorTools(BASELINES).run("lookup_vendor", json.dumps({"query": query}))
    assert out.get("vendor_id") == vendor_id and out["found"] is (vendor_id is not None)
    assert "bank" not in json.dumps(out).lower()


def test_lookup_vendor_rejects_oversized_input():
    tools = VendorTools(BASELINES)
    assert "error" in tools.run("lookup_vendor", json.dumps({"query": "x" * 201}))
    assert "error" in tools.run("lookup_vendor", "not json")
    assert tools.returned_vendor_ids == set()


def test_tool_agents_refuse_media(ledger):
    client = EmberClient(ledger=ledger, stage="eval", transport=FakeTransport())
    media = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:x"}]}]
    with pytest.raises(ValueError, match="text-only"):
        run_tool_agent(
            client,
            IDENTITY,
            input_items=media,
            input_kind=InputKind.EMAIL,
            item_id=None,
            toolbox=VendorTools(BASELINES),
        )


def test_an_output_limit_retry_keeps_both_calls(ledger):
    """The first, truncated call was billed: the item's receipt must count it too."""
    transport = FakeTransport(
        [ok_response(status="incomplete", text="{"), ok_response(text=json.dumps({"x": 1}))]
    )
    client = EmberClient(ledger=ledger, stage="eval", transport=transport)
    out = run_agent(
        client,
        DOCUMENT,
        input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        input_kind=InputKind.EMAIL,
        item_id=None,
    )
    assert len(out.earlier_records) == 1 and out.record is not None
    assert out.earlier_records[0].call_id != out.record.call_id


def test_a_partial_name_shared_by_two_vendors_lists_candidates():
    data = json.loads((ROOT / "fixtures" / "baselines.json").read_text())
    twin = {**data["vendors"][0], "vendor_id": "fennimore2", "name": "Fennimore Paper Co"}
    twin.update(aliases=[], email_domains=["fennimore-paper.example"])
    store = BaselineStore.from_dict({**data, "vendors": [*data["vendors"], twin]})
    out = VendorTools(store).run("lookup_vendor", json.dumps({"query": "Fennimore"}))
    assert out["found"] is False
    assert out["candidates"] == ["Fennimore Paper Co", "Fennimore Stationery Ltd"]


def test_trail_shows_each_lookup_and_codes_answer(ledger):
    from forgeember.web import jinja_env

    result = _screen(ledger, ToolEmber([LOOKUP, _identity()]))
    macro = jinja_env().get_template("_receipt.html").module.receipt
    html = str(macro(result.receipt(), []))
    assert "function calling" in html and "lookup_vendor(Fennimore Stationery)" in html
    assert "found <b>Fennimore Stationery Ltd</b>" in html
    assert "claims to be one of our vendors" in html
