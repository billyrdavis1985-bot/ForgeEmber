"""Duplicate invoices: the same charge twice, caught from the hold history (cases.py)."""

import json
import shutil
from pathlib import Path

import pytest

from forgeember import envelope, fixturegen
from forgeember.actions import HoldQueue
from forgeember.baseline import BaselineStore
from forgeember.cli import _screen_and_act
from forgeember.config import Paths
from forgeember.ember.client import EmberClient
from forgeember.pipeline import Screener
from ideal_ember import IdealEmber

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")
MANIFEST = {i["id"]: i for i in json.loads((FIXTURES / "manifest.json").read_text())["items"]}
INVOICE = {"category": "invoice", "risk": "low", "held": ["pay"]}


@pytest.fixture
def repo(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "fixtures")
    paths = Paths(root=tmp_path)
    holds = HoldQueue(paths.holds_db)
    yield paths, holds
    holds.close()


def _screen(paths, holds, ledger, item, env):
    client = EmberClient(ledger=ledger, stage="demo", transport=IdealEmber(item, env))
    _screen_and_act(Screener(client=client, baselines=BASELINES), env, holds, paths)
    return json.loads((paths.receipts_dir / f"{env.id}.json").read_text())


def _fixture(paths, holds, ledger, fid):
    item = MANIFEST[fid]
    return _screen(
        paths, holds, ledger, item, envelope.from_path(paths.root / "fixtures" / item["file"])
    )


def _emailed(paths, holds, ledger, key, *, sender, name):
    """The invoice INVOICES[key] arriving again, as a PDF attached to an email."""
    pdf = fixturegen.text_pdf(fixturegen.INVOICES[key].text_lines())
    raw = fixturegen._email(
        msg_id=f"dup-{name}",
        sender=sender,
        subject=f"Invoice {fixturegen.INVOICES[key].number}",
        body="Hello,\n\nPlease find our invoice attached.\n\nThanks\n",
        date="Fri, 27 Nov 2026 09:00:00 +0000",
        attachment=(f"{name}.pdf", pdf),
    )
    path = paths.root / f"{name}.eml"
    path.write_bytes(raw)
    item = {"id": key, "scenario": "invoice again", "attack": None, "expected": INVOICE}
    return _screen(paths, holds, ledger, item, envelope.from_path(path))


FENNIMORE = "Fennimore Stationery <billing@fennimore-stationery.example>"
MARROWVALE = "Marrowvale Couriers <accounts@marrowvale-couriers.example>"


def test_the_same_invoice_sent_again_is_held_with_approve_only_one(repo, ledger):
    paths, holds = repo
    _fixture(paths, holds, ledger, "inv-001")  # FS-2026-1182, 634.80, waiting
    receipt = _emailed(paths, holds, ledger, "inv-001", sender=FENNIMORE, name="again")
    assert receipt["duplicate"]["kind"] == "resent" and receipt["risk"] == "medium"
    assert "this invoice may already be waiting for approval or paid" in receipt["reasons"]
    first, second = [h for h in holds.list() if h.invoice_ref == "FS-2026-1182"]
    assert second.next_step == (
        "Invoice FS-2026-1182 for 634.80 is already waiting for approval: approve only one of "
        "the two."
    )
    assert second.risk == "medium" and second.needs_verification
    assert first.next_step.startswith("A later invoice may duplicate this one")
    assert first.needs_verification  # the earlier hold can't be waved through either


def test_an_invoice_already_approved_is_flagged_when_it_comes_back(repo, ledger):
    paths, holds = repo
    _fixture(paths, holds, ledger, "inv-007")  # MVC-1047
    [pay] = holds.list()
    holds.decide(pay.hold_id, approve=True, decided_by="Billy Davis")
    receipt = _emailed(paths, holds, ledger, "inv-007", sender=MARROWVALE, name="paid")
    assert receipt["duplicate"]["kind"] == "already_approved"
    [new] = holds.list()  # the approved one is no longer pending
    assert new.next_step.startswith(
        "Invoice MVC-1047 from Marrowvale Couriers was already approved"
    )
    assert "same money twice" in new.next_step and new.needs_verification


def _renumbered(monkeypatch):
    twin = fixturegen._fennimore("FS-2026-1299")  # same lines, same 634.80, new number
    monkeypatch.setitem(fixturegen.INVOICES, "twin", twin)


def test_the_same_amount_under_a_new_number_days_later_is_flagged(repo, ledger, monkeypatch):
    paths, holds = repo
    _renumbered(monkeypatch)
    _fixture(paths, holds, ledger, "inv-001")
    receipt = _emailed(paths, holds, ledger, "twin", sender=FENNIMORE, name="twin")
    assert receipt["duplicate"]["kind"] == "renumbered"
    new = next(h for h in holds.list() if h.invoice_ref == "FS-2026-1299")
    assert new.next_step == (
        "Fennimore Stationery Ltd sent invoice FS-2026-1182 for the same 634.80 earlier today: "
        "check this isn't the same charge under a new number before paying."
    )
    assert new.draft is not None and "FS-2026-1299" in new.draft["body"]


def test_a_monthly_bill_of_the_same_amount_is_not_a_duplicate(repo, ledger, monkeypatch):
    paths, holds = repo
    _renumbered(monkeypatch)
    _fixture(paths, holds, ledger, "inv-001")
    with holds._lock:  # last month's invoice: filed 30 days ago
        holds._conn.execute("UPDATE holds SET created_at = '2026-10-28T09:00:00+00:00'")
    receipt = _emailed(paths, holds, ledger, "twin", sender=FENNIMORE, name="monthly")
    assert receipt["duplicate"] is None and receipt["risk"] == "low"


def test_rejected_payments_and_other_vendors_never_count(repo, ledger):
    paths, holds = repo
    _fixture(paths, holds, ledger, "inv-001")
    [pay] = holds.list()
    holds.decide(pay.hold_id, approve=False, decided_by="Billy Davis")
    receipt = _emailed(paths, holds, ledger, "inv-001", sender=FENNIMORE, name="rejected")
    assert receipt["duplicate"] is None and receipt["risk"] == "low"


def test_a_correction_stays_on_the_amount_path_not_the_duplicate_one(repo, ledger):
    paths, holds = repo
    _fixture(paths, holds, ledger, "inv-001")  # 634.80
    receipt = _fixture(paths, holds, ledger, "em-009")  # the corrected 709.80
    assert receipt["duplicate"] is None
    assert receipt["followup"]["changes"] == ["amount"]


def test_a_resent_copy_says_so_on_the_link(repo, ledger):
    paths, holds = repo
    _fixture(paths, holds, ledger, "inv-001")
    receipt = _fixture(paths, holds, ledger, "em-010")  # the copy, by email
    assert receipt["followup"]["note"] == (
        "An email brought invoice FS-2026-1182 again, for the same 634.80: the same charge "
        "twice. Approve only one of the two holds."
    )
