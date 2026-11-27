"""Follow-ups on open payment holds: link a new message to the one hold it is about.

Code links, and only when exactly one open invoice fits:

* **its invoice number is mentioned**: the hold's invoice number appears in the item's text,
  its voicemail transcript or its extracted invoice number, as a whole token. "MVC-1047"
  also matches a spoken "1047" (the trailing digits, 4 or more), and
* **same vendor**, when code resolved one for the new item (a sender domain on file, the
  vendor named on an invoice, or a company heard in a voicemail). When it resolved none (a
  company name misheard in a voicemail, a lookalike domain), the invoice number alone links,
  across all vendors' open invoices. That is the case that matters most: someone asking to
  divert payment of an invoice that is really waiting. (Live run, Nov 26: "Marrowvale" was
  misheard, and the bank-change voicemail about 1047 failed to link.)

No candidate, or more than one invoice mentioned, means no link: the item stands alone.

A link only adds caution. The hold stays pending, its risk can rise but never fall (a claimed
change makes it at least MEDIUM), and the note says what changed and what to verify, written
by code from verified facts. Ember's words are never the note: a caller's claims stay claims.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from forgeember.actions import Hold, HoldQueue
from forgeember.baseline import BankStatus, parse_amount
from forgeember.budget.ledger import InputKind
from forgeember.pipeline import ScreenResult
from forgeember.policy import decide

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
    """The one pending hold this item is about, or None (no mention, another vendor's
    invoice, or more than one invoice fits)."""
    texts = _texts(result)
    open_holds = [
        h
        for h in holds.list(status="pending")
        if h.invoice_ref
        and h.item_id != result.envelope.id
        and (result.vendor_id is None or h.vendor_id == result.vendor_id)
        and mentions(h.invoice_ref, texts)
    ]
    invoices = {(h.vendor_id, h.invoice_ref) for h in open_holds}
    if len(invoices) != 1:
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


def channel_of(result: ScreenResult) -> str:
    """How the item arrived, for the owner: "A voicemail", "An email", "A screen recording"
    or "A document"."""
    f = result.facts
    if f is not None and f.voicemail:
        return "A voicemail"
    if result.envelope.kind is InputKind.EMAIL:
        return "An email"
    if f is not None and f.video:
        return "A screen recording"
    return "A document"


def note_for(result: ScreenResult, hold: Hold, changes: tuple[str, ...]) -> str:
    """One sentence for the owner, from code's facts: what arrived, what it claims, what to do."""
    ref = hold.invoice_ref
    channel = channel_of(result)
    heard = channel == "A voicemail"
    who = "A caller" if heard else channel  # who asked: a person on a call, or the message
    if "bank_details" in changes:
        if heard:
            source = "The caller's identity and spoken digits are unverified."
        elif channel == "A screen recording":
            source = "Who made it is unknown, and the details on screen are unverified."
        elif result.code.sender_vendor is not None and result.code.sender_vendor.vendor_id == (
            hold.vendor_id
        ):
            source = (
                "It came from the supplier's domain on file, which is not proof: mailboxes "
                "get taken over."
            )
        else:
            source = "It did not come from a domain on file for the supplier."
        verb = "shows" if channel == "A screen recording" else "requested"
        return (
            f"{who} {verb} new bank details for invoice {ref}. {source} Call the supplier "
            "using the number on file before approving payment."
        )
    if "amount" in changes:
        return (
            f"{channel} gives a different amount for invoice {ref}: {result.amount} instead of "
            f"{hold.amount}. Confirm it with the supplier before approving payment."
        )
    dup = result.duplicate
    if dup is not None and dup.kind == "resent" and dup.hold_id == hold.hold_id:
        note = (
            f"{channel} brought invoice {ref} again, for the same {hold.amount}: the same "
            "charge twice. Approve only one of the two holds."
        )
        # A resend that is itself suspect for other reasons: keep the double-charge
        # warning and say so, like the branch below. The resend alone (MEDIUM via the
        # duplicate fact) doesn't count as suspicion.
        others = (
            decide(replace(result.facts, possible_duplicate=False))
            if result.facts is not None
            else None
        )
        if others is not None and others.risk.value != "low":
            why = f" ({others.reasons[0]})" if others.reasons else ""
            note += (
                f" It was itself rated {others.risk.value.upper()}{why}: don't act on "
                "anything it says."
            )
        return note
    d = result.decision
    if d is not None and d.risk.value != "low":
        # Live, Nov 26: a lookalike-domain email about an open invoice linked as a routine
        # update ("Nothing new to verify"). The message itself is suspect, so say so.
        why = f" ({d.reasons[0]})" if d.reasons else ""
        return (
            f"{channel} about invoice {ref} was itself rated {d.risk.value.upper()}{why}. It "
            "asks for no change to the payment details or amount, but don't act on anything "
            "it says."
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
    # A claimed change to the payment details or amount is at least MEDIUM on the hold it
    # changes, even when the new message looks routine on its own.
    floor = "medium" if changes else "low"
    risk = max(hold.risk, result.decision.risk.value, floor, key=lambda r: _RANK.get(r, 2))
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


# ----------------------------------------------------------------------------- duplicates
# The same charge twice is the plainest way a small business loses money. Code checks the
# hold history before an invoice's own payment hold is filed:
#
# * "already_approved": a payment for this vendor and invoice number was already approved;
# * "resent": the same vendor, invoice number and amount is already waiting;
# * "renumbered": the same vendor and amount under another invoice number, within a short
#   window (monthly bills of the same amount are about 30 days apart, so they don't trip it).
#
# A same-number invoice with a different amount is a correction: the follow-up path above
# handles it ("approve only one"). A duplicate only raises caution, like a link.
DUPLICATE_WINDOW_DAYS = 14


@dataclass(frozen=True, slots=True)
class Duplicate:
    kind: str  # "already_approved", "resent" or "renumbered"
    hold_id: int
    item_id: str
    invoice_ref: str | None
    amount: str | None
    status: str
    when: str  # the other hold's decision time if approved, else when it was filed
    days_apart: int


def _days_between(a: str, b: str) -> int:
    def parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)

    return abs(parse(a) - parse(b)).days  # abs first: a negative delta's .days rounds down


def find_duplicate(result: ScreenResult, holds: HoldQueue, *, now: str) -> Duplicate | None:
    """The earlier payment this invoice repeats, if any. Needs a vendor code resolved and a
    payment held; rejected holds don't count."""
    if result.vendor_id is None or result.decision is None:
        return None
    if not any(a.kind == "pay" for a in result.decision.held):
        return None
    amount = parse_amount(result.amount)
    found: list[Duplicate] = []
    for h in holds.list(status=None):
        if (
            h.action != "pay"
            or h.status == "rejected"
            or h.vendor_id != result.vendor_id
            or h.item_id == result.envelope.id
        ):
            continue
        same_ref = bool(result.invoice_ref) and h.invoice_ref == result.invoice_ref
        same_amount = amount is not None and parse_amount(h.amount) == amount
        days = _days_between(h.created_at, now)
        kind = None
        if same_ref and h.status == "approved":
            kind = "already_approved"
        elif same_ref and same_amount:
            kind = "resent"
        elif not same_ref and same_amount and days <= DUPLICATE_WINDOW_DAYS:
            kind = "renumbered"
        if kind:
            when = h.decided_at if h.status == "approved" and h.decided_at else h.created_at
            found.append(
                Duplicate(kind, h.hold_id, h.item_id, h.invoice_ref, h.amount, h.status, when, days)
            )
    order = {"already_approved": 0, "resent": 1, "renumbered": 2}
    return min(found, key=lambda d: (order[d.kind], d.days_apart), default=None)


def mark_duplicate(result: ScreenResult, holds: HoldQueue) -> Duplicate | None:
    """Record a duplicate as a fact and let policy decide again (it can only rise)."""
    dup = find_duplicate(result, holds, now=datetime.now(UTC).isoformat())
    if dup is None or result.facts is None:
        return None
    result.duplicate = dup
    result.facts = replace(result.facts, possible_duplicate=True)
    result.decision = decide(result.facts)
    return dup
