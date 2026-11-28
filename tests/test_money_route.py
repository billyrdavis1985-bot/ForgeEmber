"""The money route's backstop (moneytext + Screener._money_floor): a payment request that
triage misfiles as ordinary mail still gets its payment hold, because code reads the text too.

Before the floor, every email-only money fixture lost its pay or bank-change hold when triage
gave it a wrong route (36 of 120 misroutes). Documents, voicemails and videos were already
safe: code extracts every document and listens to every recording, whatever triage says."""

import json
from pathlib import Path

import pytest

from attack_ember import BlindEmber
from forgeember import envelope
from forgeember.attackgen import IBAN_ATTACKER, ZW
from forgeember.baseline import BankStatus, BaselineStore
from forgeember.ember.client import EmberClient
from forgeember.fixturegen import spaced
from forgeember.moneytext import money_in
from forgeember.pipeline import Screener, ScreenResult, code_findings
from ideal_ember import IdealEmber

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")
WRONG_ROUTES = ("normal_business", "other", "marketing", "receipt")
MONEY_ROUTES = {"invoice", "payment_request", "executive_request", "bank_change_request"}
RANK = {"low": 0, "medium": 1, "high": 2}
NOT_AN_INVOICE = {
    "is_invoice": False,
    "vendor_name": None,
    "vendor_email": None,
    "invoice_number": None,
    "invoice_date": None,
    "due_date": None,
    "currency": None,
    "line_items": [],
    "subtotal": None,
    "tax": None,
    "total": None,
    "bank": None,
    "claims_bank_change": False,
    "evidence": [],
}


def _manifest(directory: Path) -> list[dict]:
    return json.loads((directory / "manifest.json").read_text())["items"]


# ----------------------------------------------------------------------------- the detector
@pytest.mark.parametrize(
    "text",
    [
        "IBAN: GB03 ZZTB 6091 7233 0198 44",
        f"IBAN: {ZW.join(['GB91', 'ZZTB', '8300', '4417', '7620', '51'])}",  # at-07's spacing
        f"Pay to {spaced(IBAN_ATTACKER)} thank you",  # unlabelled, but the checksum holds
        "Account number: 4417 7620, sort code 20-45-77",
        "Routing number: 026009593",
    ],
)
def test_payment_details_are_found_in_the_text(text):
    assert money_in([text]).payment_details


@pytest.mark.parametrize(
    "text",
    [
        "Your statement for the account ending 4417 is ready.",
        "Ref AB12 3456 7890 1234 56 for your order",  # IBAN-shaped, no label, bad checksum
        "Our phone number stays 01632 960481.",
    ],
)
def test_numbers_that_are_not_payment_details(text):
    assert not money_in([text]).payment_details


@pytest.mark.parametrize(
    "text",
    [
        "Please pay invoice KBH-88412 by Friday.",
        "Our records show an outstanding balance of GBP 398.80 on your account.",
        "Invoice MVC-20957 is overdue; please confirm payment today.",
        "I need you to send GBP 4,800 to a new flour supplier today.",
        "Amount due: GBP 12.00",
        "Your account is \N{CYRILLIC SMALL LETTER O}verdue: GBP 40.00",  # a look-alike letter
    ],
)
def test_payment_demands_are_found_in_the_text(text):
    assert money_in([text]).payment_demand


@pytest.mark.parametrize(
    "text",
    [
        "We've paid GBP 420.00 for order 5512 by bank transfer today.",  # money coming in
        "We've raised credit note CN-0042 for GBP 37.50. Nothing for you to do.",
        "Could I order 20 cinnamon buns? Happy to pay in the shop when I collect.",
        "Receipt for invoice INV-2231. Amount due: GBP 0.00. Thank you for your payment.",
        "Balance due: $0",
        "Our prices rise by 4% from January.",
        "This invoice is overdue.",  # no amount and no invoice number: nothing to pay
    ],
)
def test_reports_receipts_and_mentions_are_not_demands(text):
    assert not money_in([text]).payment_demand


def test_a_bank_change_needs_payment_details_said_to_be_new():
    moved = "We have moved our banking to a new provider. IBAN: GB03 ZZTB 6091 7233 0198 44"
    assert money_in([moved]).bank_change
    assert not money_in(
        ["From 1 December please update your records with our new email."]
    ).bank_change
    assert not money_in(
        ["Please pay by bank transfer. IBAN: GB03 ZZTB 6091 7233 0198 44"]
    ).bank_change
    signup = money_in(["Welcome! Your new account is ready. Account number: 88123456"])
    assert signup.payment_details and not signup.bank_change  # a sign-up, not a bank change


def test_no_raised_route_is_claimed_when_triage_never_ran(ledger):
    """A budget stop before triage: the floor still keeps the money route (conservative), but
    there was no triage route to correct, so nothing is recorded as raised."""
    item = next(i for i in _manifest(FIXTURES) if i["id"] == "em-006")
    env = envelope.from_path(FIXTURES / item["file"])
    screener = Screener(client=None, baselines=BASELINES)  # type: ignore[arg-type]
    result = ScreenResult(envelope=env, code=code_findings(env, BASELINES))
    screener._verify_and_decide(result)
    assert result.facts.category == "payment_request" and not result.facts.route_raised
    assert "pay" in {a.kind for a in result.decision.held}
    assert not result.notes


# ----------------------------------------------------------------------------- the sweep
def _misrouted(base: type, route: str) -> type:
    """Ember with triage wrong: the given route, no signals, no document wanted. Everything
    else it reads faithfully; a letter it is asked to extract, it reads as not an invoice."""

    class Misrouted(base):
        def triage_result(self, body):
            out = super().triage_result(body)
            out.update(
                category=route, risk_signals=[], escalate=False, needs_document_extraction=False
            )
            return out

        def invoice_extraction(self, body):
            try:
                return super().invoice_extraction(body)
            except KeyError:
                return NOT_AN_INVOICE

        def risk_assessment(self, body):
            return {"verdict": "benign", "signals": [], "rationale": "nothing noticed"}

    return Misrouted


MONEY_ITEMS = [
    pytest.param(directory, base, item, id=item["id"])
    for directory, base in ((FIXTURES, IdealEmber), (FIXTURES / "attacks", BlindEmber))
    for item in _manifest(directory)
    if {"pay", "change_bank_details"} & set(item["expected"].get("held", []))
]


def _screen(ledger, directory, ember_cls, item):
    env = envelope.from_path(directory / item["file"])
    transport = ember_cls(item, env)
    client = EmberClient(ledger=ledger, stage="eval", transport=transport)
    return Screener(client=client, baselines=BASELINES).screen(env), transport


@pytest.mark.parametrize("route", WRONG_ROUTES)
@pytest.mark.parametrize(("directory", "base", "item"), MONEY_ITEMS)
def test_a_misrouted_money_item_keeps_its_money_holds(ledger, directory, base, item, route):
    result, _ = _screen(ledger, directory, _misrouted(base, route), item)
    want = {"pay", "change_bank_details"} & set(item["expected"]["held"])
    held = {a.kind for a in result.decision.held}
    assert want <= held, f"{item['id']} as {route}: lost {sorted(want - held)}"
    expected_risk = item["expected"].get("risk") or item["expected"]["min_risk"]
    if expected_risk != "low":
        assert result.decision.risk.value != "low", f"{item['id']} as {route}: rated LOW"


def test_the_sweep_covers_every_email_only_money_item():
    ids = {p.values[2]["id"] for p in MONEY_ITEMS}
    assert len(ids) == 30
    assert {
        "em-001",
        "em-002",
        "em-006",
        "em-008",
        "at-01",
        "at-02",
        "at-07",
        "at-09",
        "at-10",
    } <= ids


def test_a_raised_route_is_recorded(ledger):
    item = next(i for i in _manifest(FIXTURES) if i["id"] == "em-006")
    result, _ = _screen(ledger, FIXTURES, _misrouted(IdealEmber, "normal_business"), item)
    assert result.facts.route_raised and result.facts.category == "payment_request"
    assert any("triage filed this as normal_business" in n for n in result.notes)
    right, _ = _screen(ledger, FIXTURES, IdealEmber, item)  # triage right: nothing to raise
    assert not right.facts.route_raised and not right.notes


def test_a_misrouted_invoice_email_is_read_so_its_account_is_checked(ledger):
    """at-07 filed as ordinary mail: the text gives an account, so code asks for the invoice
    to be read, and compares the account with the one on file for the vendor."""
    item = next(i for i in _manifest(FIXTURES / "attacks") if i["id"] == "at-07")
    result, transport = _screen(
        ledger, FIXTURES / "attacks", _misrouted(BlindEmber, "normal_business"), item
    )
    asked = [
        body["text"]["format"]["name"] for path, body in transport.calls if path == "/responses"
    ]
    assert "invoice_extraction" in asked
    assert result.facts.category == "invoice" and result.facts.bank_status is BankStatus.CHANGED
    assert result.decision.risk.value == "high"


# ----------------------------------------------------------------------------- live results
def _live_ordinary() -> dict[str, dict]:
    report = sorted((ROOT / "traces" / "ordinary").glob("ordinary-*.json"))[-1]
    return {row["id"]: row for row in json.loads(report.read_text())["items"]}


def test_the_floor_changes_no_route_the_live_runs_gave():
    """The committed live results stand: on every fixture, the route the live run gave (Gate A
    routed 25/25 as labelled; the ordinary set's routes are in its report) is one the floor
    keeps. A wider detector that would change one has to be re-measured live."""
    live = _live_ordinary()
    checked = 0
    for directory in (FIXTURES, FIXTURES / "attacks", FIXTURES / "ordinary"):
        for item in _manifest(directory):
            outcome = live.get(item["id"]) or item["expected"]
            route = outcome["category"]
            code = code_findings(envelope.from_path(directory / item["file"]), BASELINES)
            assert Screener._money_floor(route, code) == route, item["id"]
            if code.money.bank_change:  # code's bank-change claim: only where one was held
                assert "change_bank_details" in outcome["held"], item["id"]
            checked += 1
    assert checked == 60
