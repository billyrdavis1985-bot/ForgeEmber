"""Handoff: the next step on every hold, a draft to the vendor, and a recorded verification."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgeember import envelope
from forgeember.actions import HoldQueue
from forgeember.baseline import BaselineStore
from forgeember.cli import _screen_and_act
from forgeember.config import Paths
from forgeember.console.app import create_app
from forgeember.ember.client import EmberClient
from forgeember.handoff import draft_problems, next_steps
from forgeember.pipeline import Screener
from forgeember.schemas import DraftMessage
from ideal_ember import IdealEmber

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")
MANIFEST = {i["id"]: i for i in json.loads((FIXTURES / "manifest.json").read_text())["items"]}


def _screen(fid, ledger, transport_cls=IdealEmber):
    item = MANIFEST[fid]
    env = envelope.from_path(FIXTURES / item["file"])
    ember = transport_cls(item, env)
    client = EmberClient(ledger=ledger, stage="demo", transport=ember)
    return Screener(client=client, baselines=BASELINES).screen(env), ember


@pytest.mark.parametrize(
    ("fid", "action", "says", "check"),
    [
        ("inv-001", "pay", "Matches Fennimore Stationery Ltd's account on file", False),
        ("inv-002", "change_bank_details", "Call Fennimore Stationery Ltd on 01632 960150", True),
        ("inv-004", "pay", "isn't one of your vendors", True),
        ("inv-005", "pay", "The totals don't add up", True),
        ("em-001", "change_bank_details", "lookalike of Fennimore Stationery Ltd's domain", True),
        ("em-002", "pay", "Confirm with Dana Whitlock in person", True),
        ("em-007", "pay", "instructions aimed at automated reviewers", True),
        ("em-008", "pay", "It claims to be Fennimore Stationery Ltd", True),
        ("em-006", "pay", "answer only through Marrowvale Couriers' address on file", True),
        ("inv-007", "pay", "Matches Marrowvale Couriers' account on file", False),
        ("vm-002", "change_bank_details", "The caller gave 01632960777, which is not that", True),
        ("vm-003", "pay", "Confirm with Dana Whitlock in person", True),
    ],
)
def test_every_hold_gets_a_next_step_from_facts(fid, action, says, check, ledger):
    result, _ = _screen(fid, ledger)
    step = next_steps(result, BASELINES)[action]
    assert says in step.text and step.needs_verification is check


def _repo(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "fixtures")
    return Paths(root=tmp_path)


def _act(fid, ledger, paths, holds, transport_cls=IdealEmber):
    item = MANIFEST[fid]
    env = envelope.from_path(paths.root / "fixtures" / item["file"])
    ember = transport_cls(item, env)
    client = EmberClient(ledger=ledger, stage="demo", transport=ember)
    _screen_and_act(Screener(client=client, baselines=BASELINES), env, holds, paths)
    receipt = json.loads((paths.receipts_dir / f"{env.id}.json").read_text())
    return env, receipt, ember


def test_the_drafter_never_sees_the_message_and_writes_to_the_address_on_file(tmp_path, ledger):
    paths = _repo(tmp_path)
    holds = HoldQueue(paths.holds_db)
    env, receipt, ember = _act("inv-002", ledger, paths, holds)  # Fennimore, new bank account

    [draft_call] = [
        b
        for p, b in ember.calls
        if b.get("text", {}).get("format", {}).get("name") == "draft_message"
    ]
    sent = json.dumps(draft_call["input"])
    ours = {BASELINES.organization.name, *(v.name for v in BASELINES.vendors)}  # our records
    for part in env.parts:  # nothing from the item itself reaches the drafter
        for line in (part.text or "").splitlines():
            if len(line.strip()) > 12 and line.strip() not in ours:
                assert line.strip() not in sent, line
    assert "60917233019844" not in sent  # the new account number
    assert receipt["handoff"]["draft"]["to"] == "accounts@fennimore-stationery.example"
    [bank] = [h for h in holds.list() if h.action == "change_bank_details"]
    assert bank.draft["to"] == "accounts@fennimore-stationery.example"
    assert any(c["agent_role"] == "handoff" for c in receipt["calls"])  # cost on this receipt
    holds.close()


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("Please confirm at https://example.org/x", "a link"),
        ("Reply to ar@fmr-supplies.example please", "an email address"),
        ("Call us on 01632 960735 to confirm", "a long number"),
        ("Is GB12ZZTB30941255018273 your new account?", "an IBAN"),
        (" ".join(["word"] * 130), "longer than"),
    ],
)
def test_code_rejects_drafts_that_carry_contact_or_account_details(body, problem):
    problems = draft_problems(DraftMessage(subject="Please confirm", body=body))
    assert any(problem in p for p in problems)
    clean = DraftMessage(subject="Please confirm", body="We received a request about MVC-1047.")
    assert draft_problems(clean) == []
    # an invoice number code supplied may appear; a number the model added may not
    ours = DraftMessage(subject="Invoice FS-2026-1190", body="About invoice FS-2026-1190.")
    assert draft_problems(ours, allowed=("FS-2026-1190",)) == []
    theirs = DraftMessage(subject="x", body="About FS-2026-1190, account 55018273.")
    assert draft_problems(theirs, allowed=("FS-2026-1190",)) == [
        "contains a long number (phone or account)"
    ]


def test_a_rejected_draft_is_dropped_and_noted(tmp_path, ledger):
    class LeakyDrafter(IdealEmber):
        def draft_message(self, body):
            return {"subject": "Confirm", "body": "Please call 01632 960735 to confirm."}

    paths = _repo(tmp_path)
    holds = HoldQueue(paths.holds_db)
    _, receipt, _ = _act("inv-002", ledger, paths, holds, LeakyDrafter)
    assert receipt["handoff"]["draft"] is None
    assert any("rejected by code checks" in n for n in receipt["handoff"]["notes"])
    assert all(h.draft is None for h in holds.list())
    holds.close()


def test_approving_a_checked_hold_needs_a_written_verification(tmp_path):
    holds = HoldQueue(tmp_path / "holds.sqlite")
    checked = holds.add(
        item_id="i1", action="pay", risk="high", reason="r", reasons=(), needs_verification=True
    )
    routine = holds.add(item_id="i2", action="pay", risk="low", reason="r", reasons=())
    with pytest.raises(ValueError, match="how you verified"):
        holds.decide(checked, approve=True, decided_by="Billy Davis", verification="  ")
    done = holds.decide(
        checked,
        approve=True,
        decided_by="Billy Davis",
        verification="Called 01632 960481 on file; account unchanged",
    )
    assert done.status == "approved" and done.verification.startswith("Called")
    assert holds.decide(routine, approve=True, decided_by="Billy Davis").status == "approved"
    other = holds.add(
        item_id="i3", action="pay", risk="high", reason="r", reasons=(), needs_verification=True
    )
    assert holds.decide(other, approve=False, decided_by="Billy Davis").status == "rejected"
    holds.close()


def test_invoice_1047_handoff_end_to_end_and_in_the_console(tmp_path, ledger):
    paths = _repo(tmp_path)
    holds = HoldQueue(paths.holds_db)
    _act("inv-007", ledger, paths, holds)
    [pay] = holds.list()
    assert pay.next_step.startswith("Matches Marrowvale Couriers") and not pay.needs_verification

    _, receipt, _ = _act("vm-005", ledger, paths, holds)  # new bank details for 1047
    pay = holds.get(pay.hold_id)
    assert pay.needs_verification and pay.risk == "high"
    assert pay.next_step.startswith("Call Marrowvale Couriers on 01632 960481")
    assert "The caller gave 01632960735, which is not that number." in pay.next_step
    assert pay.draft["to"] == "accounts@marrowvale-couriers.example"
    assert "invoice MVC-1047" in pay.draft["body"]
    assert receipt["handoff"]["linked_next_step"]["text"] == pay.next_step
    holds.close()

    console = TestClient(create_app(paths))
    page = console.get("/").text
    assert "Next:</b> Call Marrowvale Couriers on 01632 960481" in page
    assert "How did you verify?" in page and "Draft to accounts@marrowvale-couriers.example" in page
    hx = {"HX-Request": "true"}
    url = f"/holds/{pay.hold_id}/approve"
    assert console.post(url, data={"decided_by": "Billy Davis"}, headers=hx).status_code == 400
    ok = console.post(
        url,
        data={"decided_by": "Billy Davis", "verification": "Called 01632 960481; no change made"},
        headers=hx,
    )
    assert ok.status_code == 200 and "verified: Called 01632 960481" in ok.text


def test_rescreening_the_original_invoice_keeps_the_caution_a_follow_up_added(tmp_path, ledger):
    """Review, Nov 26: a routine rescreen of invoice 1047 overwrote the hold's risk, its call
    instruction and its need for a recorded check with the original LOW values."""
    paths = _repo(tmp_path)
    holds = HoldQueue(paths.holds_db)
    _act("inv-007", ledger, paths, holds)
    _act("vm-005", ledger, paths, holds)  # new bank details for 1047: HIGH, call first
    [pay] = [h for h in holds.list() if h.invoice_ref == "MVC-1047"]
    before = holds.get(pay.hold_id)

    _act("inv-007", ledger, paths, holds)  # the same invoice, screened again: LOW on its own
    after = holds.get(pay.hold_id)
    assert [h.hold_id for h in holds.list() if h.invoice_ref == "MVC-1047"] == [pay.hold_id]
    assert after.risk == "high" and after.needs_verification
    assert after.next_step == before.next_step
    assert after.next_step.startswith("Call Marrowvale Couriers on 01632 960481")
    with pytest.raises(ValueError, match="how you verified"):
        holds.decide(after.hold_id, approve=True, decided_by="Billy Davis")
    holds.close()


def test_a_rescreen_still_refreshes_a_hold_nothing_escalated(tmp_path):
    holds = HoldQueue(tmp_path / "holds.sqlite")
    kw = {"item_id": "i1", "action": "pay", "reason": "r", "reasons": ()}
    hid = holds.add(risk="low", next_step="Approve when it's due.", **kw)
    assert (
        holds.add(risk="medium", next_step="Check the scan.", needs_verification=True, **kw) == hid
    )
    raised = holds.get(hid)
    assert (raised.risk, raised.next_step, raised.needs_verification) == (
        "medium",
        "Check the scan.",
        True,
    )
    holds.add(risk="low", next_step="Approve when it's due.", **kw)  # never back down
    kept = holds.get(hid)
    assert (kept.risk, kept.next_step, kept.needs_verification) == (
        "medium",
        "Check the scan.",
        True,
    )
    holds.close()


@pytest.mark.parametrize("name", ["invoice-1047", "corrected-invoice"])
def test_scenario_specs_hold_with_ideal_answers(name, tmp_path, ledger):
    """Each scenario's expected links and hold states, run the way `forgeember scenario` does."""
    from forgeember.cli import check_holds

    spec = json.loads((FIXTURES / "scenarios" / f"{name}.json").read_text())
    paths = _repo(tmp_path)
    holds = HoldQueue(paths.holds_db)
    item_ids = {}
    for step in spec["steps"]:
        env, receipt, _ = _act(step["fixture"], ledger, paths, holds)
        item_ids[step["fixture"]] = env.id
        followup = receipt["followup"]
        linked = next(
            (f for f, i in item_ids.items() if followup and i == followup["hold_item_id"]), None
        )
        assert linked == step["links_to"], step["fixture"]
        assert sorted(followup["changes"] if followup else []) == sorted(step.get("changes", []))
        for check in check_holds(step.get("expect", []), holds, item_ids):
            assert check["ok"], (step["fixture"], check["problems"])
    holds.close()


def test_a_corrected_amount_gets_its_own_instruction_not_a_bank_call(tmp_path, ledger):
    """Review, Nov 26: an amount change was routed through the bank-verification step."""
    paths = _repo(tmp_path)
    holds = HoldQueue(paths.holds_db)
    _act("inv-001", ledger, paths, holds)  # FS-2026-1182, 634.80, routine
    _, receipt, _ = _act("em-009", ledger, paths, holds)  # corrected: 709.80
    pays = [h for h in holds.list() if h.action == "pay" and h.invoice_ref == "FS-2026-1182"]
    assert len(pays) == 2  # the original and the corrected invoice
    expected = (
        "An email gives 709.80 for invoice FS-2026-1182, not the 634.80 on the invoice already "
        "waiting. Check the revised charge against your order and confirm it with Fennimore "
        "Stationery Ltd on 01632 960150, the number in your records, then approve only one of "
        "the two holds for this invoice."
    )
    for hold in pays:
        assert hold.next_step == expected and hold.needs_verification
        assert "bank" not in hold.next_step
    original = next(h for h in pays if h.amount == "634.80")
    assert original.risk == "medium"  # a claimed change is at least MEDIUM on the hold
    assert receipt["followup"]["changes"] == ["amount"]
    assert receipt["handoff"]["draft"]["to"] == "accounts@fennimore-stationery.example"
    assert "revised amount for invoice FS-2026-1182" in json.dumps(receipt["handoff"])
    holds.close()


def test_a_later_follow_up_never_erases_an_earlier_check(tmp_path, ledger):
    from forgeember.cli import _with_earlier_check

    holds = HoldQueue(tmp_path / "holds.sqlite")
    kw = {"item_id": "i1", "action": "pay", "risk": "high", "reason": "r", "reasons": ()}
    hid = holds.add(next_step="Call Marrowvale on file.", needs_verification=True, **kw)
    merged = _with_earlier_check(holds.get(hid), "Check the revised 709.80.")
    assert merged == "Call Marrowvale on file. Also: Check the revised 709.80."
    assert (
        _with_earlier_check(holds.get(hid), "Call Marrowvale on file.")
        == "Call Marrowvale on file."
    )
    routine = holds.add(
        item_id="i2",
        action="pay",
        risk="low",
        reason="r",
        reasons=(),
        next_step="Approve when it's due.",
    )
    assert _with_earlier_check(holds.get(routine), "Check the revised 709.80.") == (
        "Check the revised 709.80."
    )
    holds.close()
