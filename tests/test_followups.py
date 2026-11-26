"""Follow-ups on open payment holds: code links a later message to the one hold it is about."""

import json
import sqlite3
from pathlib import Path

import pytest

from forgeember import envelope
from forgeember.actions import HoldQueue, carry_out
from forgeember.baseline import BaselineStore
from forgeember.cases import link_followup, mentions
from forgeember.ember.client import EmberClient
from forgeember.pipeline import Screener
from ideal_ember import IdealEmber

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")
MANIFEST = {i["id"]: i for i in json.loads((FIXTURES / "manifest.json").read_text())["items"]}
SCENARIO = json.loads((FIXTURES / "scenarios" / "invoice-1047.json").read_text())


def _act(fid: str, ledger, holds: HoldQueue, tmp_path: Path):
    """What `forgeember screen` does per item: screen, link, then file the item's own holds."""
    item = MANIFEST[fid]
    env = envelope.from_path(FIXTURES / item["file"])
    client = EmberClient(ledger=ledger, stage="demo", transport=IdealEmber(item, env))
    result = Screener(client=client, baselines=BASELINES).screen(env)
    followup = link_followup(result, holds)
    carry_out(
        result.decision,
        item_id=env.id,
        holds=holds,
        notifications=tmp_path / "notifications.jsonl",
        vendor_id=result.vendor_id,
        invoice_ref=result.invoice_ref,
        amount=result.amount,
    )
    return env, result, followup


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("about invoice 1047.", True),
        ("invoice MVC-1047", True),
        ("invoice mvc-1047, thanks", True),
        ("pay 1047 to the new account", True),
        ("invoice 1052", False),
        ("GBP 10.47", False),
        ("total 1047.50", False),
        ("ref 21047", False),
        ("order MVC-10470", False),
        ("£1,047", False),
    ],
)
def test_mentions_whole_invoice_numbers_only(text, hit):
    assert mentions("MVC-1047", [text]) is hit


def test_invoice_1047_case_links_only_what_it_should(tmp_path, ledger):
    holds = HoldQueue(tmp_path / "holds.sqlite")
    ids, got = {}, {}
    for step in SCENARIO["steps"]:
        env, _, followup = _act(step["fixture"], ledger, holds, tmp_path)
        ids[env.id] = step["fixture"]
        got[step["fixture"]] = followup
        linked = ids.get(followup.hold_item_id) if followup else None
        assert linked == step["links_to"], step["fixture"]
        assert sorted(followup.changes if followup else []) == sorted(step.get("changes", []))

    assert got["vm-006"].note == (
        "A voicemail about invoice MVC-1047 asks for no change to the payment details or amount. "
        "Nothing new to verify."
    )
    assert got["vm-005"].note == (
        "A caller requested new bank details for invoice MVC-1047. The caller's identity and "
        "spoken digits are unverified. Call the supplier using the number on file before "
        "approving payment."
    )
    [pay] = [h for h in holds.list() if h.invoice_ref == "MVC-1047"]
    assert pay.action == "pay" and pay.status == "pending" and pay.risk == "high"  # raised
    assert [f.changes for f in holds.followups(pay.hold_id)] == [(), ("bank_details",)]
    holds.close()


def test_a_screen_recording_follow_up_is_named_as_one(tmp_path, ledger):
    """Live run, Nov 26: vd-001 linked to invoice 1047 but its note said "A document"."""
    holds = HoldQueue(tmp_path / "holds.sqlite")
    _act("inv-007", ledger, holds, tmp_path)
    *_, followup = _act("vd-001", ledger, holds, tmp_path)
    assert followup is not None and followup.changes == ("bank_details",)
    assert followup.note == (
        "A screen recording shows new bank details for invoice MVC-1047. Who made it is "
        "unknown, and the details on screen are unverified. Call the supplier using the "
        "number on file before approving payment."
    )
    holds.close()


def test_no_link_when_two_invoices_or_another_vendor_or_no_number(tmp_path, ledger):
    holds = HoldQueue(tmp_path / "holds.sqlite")
    _act("inv-007", ledger, holds, tmp_path)  # MVC-1047, Marrowvale
    for fid in ("inv-005", "inv-006"):  # two more open Marrowvale invoices
        _act(fid, ledger, holds, tmp_path)
    *_, followup = _act("vm-007", ledger, holds, tmp_path)  # mentions 1052: no such open hold
    assert followup is None
    *_, followup = _act("em-005", ledger, holds, tmp_path)  # Fennimore: another vendor
    assert followup is None
    *_, followup = _act("vm-001", ledger, holds, tmp_path)  # Marrowvale, no invoice number
    assert followup is None
    holds.close()


def test_a_message_naming_two_open_invoices_links_to_neither(tmp_path, ledger):
    holds = HoldQueue(tmp_path / "holds.sqlite")
    _act("inv-007", ledger, holds, tmp_path)
    holds.add(
        item_id="itm_other",
        action="pay",
        risk="low",
        reason="r",
        reasons=(),
        vendor_id="marrowvale",
        invoice_ref="MVC-1052",
    )
    _, vm, _ = _act("vm-006", ledger, HoldQueue(tmp_path / "unused.sqlite"), tmp_path)
    vm.voicemail = vm.voicemail.model_copy(
        update={"transcript": "About invoices 1047 and 1052: both deliveries are on Thursday."}
    )
    assert link_followup(vm, holds) is None  # ambiguous: never guess
    holds.close()


def test_a_scanned_invoice_number_code_cannot_check_is_never_a_link_target(tmp_path, ledger):
    holds = HoldQueue(tmp_path / "holds.sqlite")
    _, result, _ = _act("inv-003", ledger, holds, tmp_path)  # image: no text layer
    assert result.extraction.invoice_number and result.invoice_ref is None
    assert {h.invoice_ref for h in holds.list()} == {None}
    holds.close()


def test_a_followup_never_lowers_a_holds_risk_or_decides_it(tmp_path):
    holds = HoldQueue(tmp_path / "holds.sqlite")
    hid = holds.add(item_id="i1", action="pay", risk="high", reason="r", reasons=())
    holds.add_followup(hid, item_id="i2", risk="low", changes=(), note="n")
    [h] = holds.list()
    assert (h.risk, h.status) == ("high", "pending")
    holds.decide(hid, approve=False, decided_by="Billy Davis")
    with pytest.raises(ValueError, match="not pending"):
        holds.add_followup(hid, item_id="i3", risk="high", changes=(), note="n")
    holds.close()


def test_an_older_hold_database_gains_the_new_columns(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE holds (hold_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, "
        "action TEXT NOT NULL, risk TEXT NOT NULL, reason TEXT NOT NULL, reasons TEXT NOT NULL, "
        "created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT, "
        "decided_at TEXT, UNIQUE (item_id, action))"
    )
    conn.execute(
        "INSERT INTO holds (item_id, action, risk, reason, reasons, created_at) "
        "VALUES ('i1', 'pay', 'low', 'r', '[]', '2026-11-24T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    holds = HoldQueue(db)
    [h] = holds.list()
    assert (h.item_id, h.vendor_id, h.invoice_ref) == ("i1", None, None)
    holds.close()


def test_console_and_site_show_the_hold_with_its_followups(tmp_path, ledger):
    import shutil

    from fastapi.testclient import TestClient

    from forgeember.cli import _screen_and_act
    from forgeember.config import Paths
    from forgeember.console.app import create_app
    from forgeember.site import build_site, export_demo

    shutil.copytree(FIXTURES, tmp_path / "fixtures")
    paths = Paths(root=tmp_path)
    holds = HoldQueue(paths.holds_db)
    ids = {}
    for step in SCENARIO["steps"]:
        item = MANIFEST[step["fixture"]]
        env = envelope.from_path(paths.root / "fixtures" / item["file"])
        ids[step["fixture"]] = env.id
        client = EmberClient(ledger=ledger, stage="demo", transport=IdealEmber(item, env))
        _screen_and_act(Screener(client=client, baselines=BASELINES), env, holds, paths)
    holds.close()

    console = TestClient(create_app(paths))
    queue = console.get("/").text
    assert "Follow-up:" in queue and "Call the supplier using the number on file" in queue
    invoice_page = console.get(f"/items/{ids['inv-007']}").text
    assert "Later messages about this invoice" in invoice_page
    vm_page = console.get(f"/items/{ids['vm-005']}").text
    assert "Follow-up on invoice MVC-1047" in vm_page
    assert "Follow-up on invoice" not in console.get(f"/items/{ids['vm-007']}").text

    export_demo(paths)
    build_site(paths, tmp_path / "site")
    site_vm = (tmp_path / "site" / "items" / "vm-005.html").read_text()
    assert 'href="../items/inv-007.html">the original invoice' in site_vm
    site_inv = (tmp_path / "site" / "items" / "inv-007.html").read_text()
    assert "Later messages about this invoice" in site_inv and "vm-006" in site_inv


def test_a_voicemail_mentioning_an_invoice_is_never_read_as_one(tmp_path, ledger):
    """Live run, Nov 25: triage asked for document extraction on vm-005, the document
    specialist read the speech as an invoice, and the follow-up never linked."""

    class EagerTriage(IdealEmber):
        def triage_result(self, body):
            return {**super().triage_result(body), "needs_document_extraction": True}

        def invoice_extraction(self, body):
            raise AssertionError("the document specialist must not read a recording")

    holds = HoldQueue(tmp_path / "holds.sqlite")
    _act("inv-007", ledger, holds, tmp_path)
    item = MANIFEST["vm-005"]
    env = envelope.from_path(FIXTURES / item["file"])
    client = EmberClient(ledger=ledger, stage="demo", transport=EagerTriage(item, env))
    result = Screener(client=client, baselines=BASELINES).screen(env)

    assert result.extraction is None and result.facts.category == "bank_change_request"
    assert [a.kind for a in result.decision.held] == ["change_bank_details"]
    followup = link_followup(result, holds)
    assert followup is not None and followup.changes == ("bank_details",)
    holds.close()
