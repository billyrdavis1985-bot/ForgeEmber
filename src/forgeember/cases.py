"""Follow-ups on open payment holds: link a new message to the one hold it is about.

Code links, and only when exactly one open invoice fits:

* **same vendor**: the new item's vendor as code resolved it (a sender domain on file, the
  vendor named on an invoice, or a company heard in a voicemail), and
* **its invoice number is mentioned**: the hold's invoice number appears in the item's text,
  its voicemail transcript or its extracted invoice number, as a whole token. "MVC-1047"
  also matches a spoken "1047" (the trailing digits, 4 or more).

No candidate, or more than one invoice mentioned, means no link: the item stands alone.

A link only adds caution. The hold stays pending, its risk can rise but never fall, and the
note says what changed and what to verify, written by code from verified facts. Ember's
words are never the note: a caller's claims stay claims.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from forgeember.actions import Hold, HoldQueue
from forgeember.baseline import BankStatus, parse_amount
from forgeember.budget.ledger import InputKind
from forgeember.pipeline import ScreenResult

MIN_REF_DIGITS = 4  # shortest digit tail that can identify an invoice on its own
_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True, slots=True)
class FollowUp:
    hold_id: int
    hold_item_id: str
    invoice_ref: str
    changes: tuple[str, ...]  # "bank_details", "amount"; empty for a routine update
    note: str
    risk: str  # the hold's risk after this follow-up


def _texts(result: ScreenResult) -> list[str]:
    texts = [p.text for p in result.envelope.parts if p.text]
    if result.voicemail is not None:
        texts.append(result.voicemail.transcript)
    if result.video is not None:
        texts += [result.video.description, *result.video.on_screen_text]
    if result.extraction is not None and result.extraction.invoice_number:
        texts.append(result.extraction.invoice_number)
    return texts


def mentions(invoice_ref: str, texts: Iterable[str]) -> bool:
    """Whole-token match of the invoice number, or of its trailing digits (4 or more)."""
    patterns = [re.compile(rf"(?<![\w-]){re.escape(invoice_ref)}(?![\w-])", re.IGNORECASE)]
    tail = re.search(r"(\d+)$", invoice_ref)
    if tail and len(tail.group(1)) >= MIN_REF_DIGITS:
        # not part of a longer number: "10.1047", "1047.50" and "21047" don't count
        patterns.append(re.compile(rf"(?<![\w-])(?<!\d[.,]){tail.group(1)}(?![\w-]|[.,]\d)"))
    return any(p.search(t) for t in texts for p in patterns)


def candidate(result: ScreenResult, holds: HoldQueue) -> Hold | None:
    """The one pending hold this item is about, or None (no vendor, no mention, or ambiguous)."""
    if result.vendor_id is None:
        return None
    texts = _texts(result)
    open_holds = [
        h
        for h in holds.list(status="pending")
        if h.vendor_id == result.vendor_id
        and h.invoice_ref
        and h.item_id != result.envelope.id
        and mentions(h.invoice_ref, texts)
    ]
    refs = {h.invoice_ref for h in open_holds}
    if len(refs) != 1:
        return None  # nothing mentioned, or two invoices mentioned: never guess
    return next((h for h in open_holds if h.action == "pay"), open_holds[0])


def changes_claimed(result: ScreenResult, hold: Hold) -> tuple[str, ...]:
    f = result.facts
    changes = []
    if f is not None and (f.claims_bank_change or f.bank_status is BankStatus.CHANGED):
        changes.append("bank_details")
    new, old = parse_amount(result.amount), parse_amount(hold.amount)
    if new is not None and old is not None and new != old:
        changes.append("amount")
    return tuple(changes)


def note_for(result: ScreenResult, hold: Hold, changes: tuple[str, ...]) -> str:
    """One sentence for the owner, from code's facts: what arrived, what it claims, what to do."""
    ref = hold.invoice_ref
    f = result.facts
    heard = f is not None and f.voicemail
    if heard:
        channel = "A voicemail"
    elif result.envelope.kind is InputKind.EMAIL:
        channel = "An email"
    else:
        channel = "A document"
    who = "A caller" if heard else channel  # who asked: a person on a call, or the message
    if "bank_details" in changes:
        if heard:
            source = "The caller's identity and spoken digits are unverified."
        elif result.code.sender_vendor is not None and result.code.sender_vendor.vendor_id == (
            hold.vendor_id
        ):
            source = (
                "It came from the supplier's domain on file, which is not proof: mailboxes "
                "get taken over."
            )
        else:
            source = "It did not come from a domain on file for the supplier."
        return (
            f"{who} requested new bank details for invoice {ref}. {source} Call the supplier "
            "using the number on file before approving payment."
        )
    if "amount" in changes:
        return (
            f"{channel} gives a different amount for invoice {ref}: {result.amount} instead of "
            f"{hold.amount}. Confirm it with the supplier before approving payment."
        )
    return (
        f"{channel} about invoice {ref} asks for no change to the payment details or amount. "
        "Nothing new to verify."
    )


def link_followup(result: ScreenResult, holds: HoldQueue) -> FollowUp | None:
    """Attach ``result`` to the open hold it's about, if exactly one fits. Raise-only."""
    hold = candidate(result, holds)
    if hold is None or result.decision is None:
        return None
    changes = changes_claimed(result, hold)
    note = note_for(result, hold, changes)
    risk = max(hold.risk, result.decision.risk.value, key=lambda r: _RANK.get(r, 2))
    holds.add_followup(
        hold.hold_id, item_id=result.envelope.id, risk=risk, changes=changes, note=note
    )
    return FollowUp(
        hold_id=hold.hold_id,
        hold_item_id=hold.item_id,
        invoice_ref=hold.invoice_ref or "",
        changes=changes,
        note=note,
        risk=risk,
    )
