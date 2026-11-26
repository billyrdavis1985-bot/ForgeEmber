"""Handoff: what the owner does next with each hold. Ember proposes; code decides; a person acts.

* **Next step** (code, no model): one line per held action, from verified facts. It names
  the contact details already on file (``baselines.json``), never ones given in the message
  under review, and says so when a caller offered a different number.
* **Draft** (Ember, minimal effort): a short verification message to the vendor's address on
  file. The drafter sees only facts code produced, never the message under review, so
  nothing in that message can steer it. Code rejects any draft carrying links, addresses or
  long digit runs. ForgeEmber never sends it.
* **Verification** (code): approving a hold whose next step asks for a check requires a
  written record of how it was checked (``HoldQueue.decide``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from forgeember.agents.base import run_agent
from forgeember.agents.specialists import DRAFTER
from forgeember.baseline import AmountStatus, BankStatus, BaselineStore, VendorProfile
from forgeember.budget.ledger import BudgetExceeded, CallRecord, InputKind
from forgeember.ember.client import EmberClient
from forgeember.pipeline import ScreenResult
from forgeember.policy import Risk
from forgeember.schemas import DraftMessage

MAX_DRAFT_WORDS = 120


@dataclass(frozen=True, slots=True)
class Step:
    text: str
    needs_verification: bool
    # Set when a message to this vendor would help: the drafter writes to its address on
    # file about ``issue`` (code's words, e.g. "a request to change your bank details").
    draft_vendor_id: str | None = None
    issue: str | None = None


@dataclass(frozen=True, slots=True)
class Draft:
    to: str
    subject: str
    body: str


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _sender(result: ScreenResult, vendor: VendorProfile | None) -> str:
    if vendor is not None:
        return vendor.name
    ext, h = result.extraction, result.envelope.headers
    if ext is not None and ext.vendor_name:
        return ext.vendor_name
    if h is not None and h.from_name:
        return h.from_name
    return "This sender"


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _on_file(vendor: VendorProfile | None) -> str:
    if vendor is not None and vendor.phone:
        return f"on {vendor.phone}, the number in your records"
    return "on a number you already have for them"


def _ref(result: ScreenResult) -> str:
    return f" for invoice {result.invoice_ref}" if result.invoice_ref else ""


def bank_step(result: ScreenResult, vendor: VendorProfile | None, ref: str = "") -> Step:
    """Bank details: verify by phone through the vendor's number on file, nothing else."""
    if vendor is None:
        return Step(
            f"Don't change any bank details: {_sender(result, vendor)} isn't one of your "
            "vendors. Reject it unless you can confirm it through a contact you already trust.",
            needs_verification=True,
        )
    text = (
        f"Call {vendor.name} {_on_file(vendor)}, not one given in this message, and confirm "
        "the account before changing anything or paying."
    )
    heard = result.voicemail.callback_number if result.voicemail else None
    if heard and vendor.phone and _digits(heard) != _digits(vendor.phone):
        text += f" The caller gave {heard}, which is not that number."
    return Step(
        text,
        needs_verification=True,
        draft_vendor_id=vendor.vendor_id,
        issue=f"a request to change your bank details{ref or _ref(result)}",
    )


def identity_step(result: ScreenResult, baselines: BaselineStore) -> Step | None:
    """Who is really asking comes first: a lookalike domain, an executive's name, a false
    vendor claim or instructions aimed at reviewers decide the step for any held action."""
    f = result.facts
    assert f is not None
    who = _sender(result, baselines.vendor_by_id(result.vendor_id))
    code = result.code
    if f.lookalike_domain and code.lookalike_of is not None:
        real = code.lookalike_of
        return Step(
            f"This came from a lookalike of {_possessive(real.name)} domain: reject it, and let "
            f"{real.name} know {_on_file(real)}.",
            needs_verification=True,
        )
    if f.executive_impersonation or f.executive_voice_request:
        execs = baselines.organization.executives
        name = code.impersonated_executive or (execs[0].name if execs else "your owner")
        return Step(
            f"Confirm with {name} in person, or on the number you already have for them, "
            "before paying anything.",
            needs_verification=True,
        )
    if f.vendor_claim_mismatch and result.identity is not None:
        claimed = baselines.vendor_by_id(result.identity.vendor_id)
        if claimed is not None:
            return Step(
                f"It claims to be {claimed.name} but comes from another domain: confirm with "
                f"{claimed.name} {_on_file(claimed)}, before paying.",
                needs_verification=True,
                draft_vendor_id=claimed.vendor_id,
                issue="a payment request in your name from an address we don't have on file",
            )
    if f.injection_attempt:
        return Step(
            "It contains instructions aimed at automated reviewers: treat it as hostile, and "
            f"check with {who} through a contact you already have before paying.",
            needs_verification=True,
        )
    return None


def pay_step(result: ScreenResult, baselines: BaselineStore) -> Step:
    """The most serious finding decides the line (same order as policy's HIGH reasons)."""
    f, d = result.facts, result.decision
    assert f is not None and d is not None
    vendor = baselines.vendor_by_id(result.vendor_id)
    who = _sender(result, vendor)
    first = identity_step(result, baselines)
    if first is not None:
        return first
    if f.bank_status is BankStatus.CHANGED or f.claims_bank_change:
        return bank_step(result, vendor)
    if f.iban_valid is False:
        return Step(
            f"The IBAN fails its checksum: don't pay this invoice; ask {who} for a corrected one.",
            needs_verification=True,
            draft_vendor_id=vendor.vendor_id if vendor else None,
            issue=f"an invoice{_ref(result)} whose bank details don't pass a validity check",
        )
    if f.math_ok is False:
        return Step(
            f"The totals don't add up: ask {who} for a corrected invoice before paying.",
            needs_verification=True,
            draft_vendor_id=vendor.vendor_id if vendor else None,
            issue=f"an invoice{_ref(result)} whose totals don't add up",
        )
    if f.key_fields_unconfirmed:
        return Step(
            "The payment details we read aren't in the document's text: check the original "
            "before paying.",
            needs_verification=True,
        )
    if f.vendor_known is False:
        return Step(
            f"{who} isn't one of your vendors: check that you ordered from them before paying.",
            needs_verification=True,
        )
    if f.amount_status is AmountStatus.UNUSUAL:
        return Step(
            f"The amount is outside {_possessive(who)} usual range: check the order before paying.",
            needs_verification=True,
            draft_vendor_id=vendor.vendor_id if vendor else None,
            issue=(
                f"invoice {result.invoice_ref} for an amount above what we usually see"
                if result.invoice_ref
                else "an invoice for an amount above what we usually see"
            ),
        )
    if f.evidence_unverifiable:
        return Step(
            "This was read from a scan or photo: check the amount and account against the "
            "original before paying.",
            needs_verification=True,
        )
    if f.heard_request:
        return Step(
            f"This was only heard in a voicemail: call {who} {_on_file(vendor)}, before paying.",
            needs_verification=True,
        )
    if f.reply_to_mismatch:
        return Step(
            "Replies to this email would go to a different domain: answer only through "
            f"{_possessive(who)} address on file, and confirm with them {_on_file(vendor)}, "
            "before paying.",
            needs_verification=True,
            draft_vendor_id=vendor.vendor_id if vendor else None,
            issue="a payment request whose replies would go to an address we don't have on file",
        )
    if not f.screening_complete or d.risk is not Risk.LOW:
        return Step(
            "Check the reasons above against the original before paying.", needs_verification=True
        )
    return Step(
        f"Matches {_possessive(who)} account on file and usual amount: approve when it's due.",
        needs_verification=False,
    )


def next_steps(result: ScreenResult, baselines: BaselineStore) -> dict[str, Step]:
    """One step per held (T2) action of this item."""
    if result.decision is None or result.facts is None:
        return {}
    steps: dict[str, Step] = {}
    vendor = baselines.vendor_by_id(result.vendor_id)
    for act in result.decision.held:
        if act.kind == "pay":
            steps["pay"] = pay_step(result, baselines)
        elif act.kind == "change_bank_details":
            steps["change_bank_details"] = identity_step(result, baselines) or bank_step(
                result, vendor
            )
        else:
            steps[act.kind] = Step(
                "Check the original yourself before allowing this.", needs_verification=True
            )
    return steps


def followup_step(
    result: ScreenResult, invoice_ref: str, vendor_id: str | None, baselines: BaselineStore
) -> Step:
    """A follow-up that claims new bank details turns the linked hold's next step into a call."""
    return bank_step(result, baselines.vendor_by_id(vendor_id), ref=f" for invoice {invoice_ref}")


# ----------------------------------------------------------------------------- drafts
_BANNED = (
    ("a link", re.compile(r"(?i)\bhttps?://|\bwww\.")),
    ("an email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("a long number (phone or account)", re.compile(r"\d(?:[\s-]?\d){5,}")),
    ("an IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,}\b")),
)


def draft_problems(draft: DraftMessage, allowed: tuple[str, ...] = ()) -> list[str]:
    """What makes a draft unsafe to hand over. ``allowed``: references code itself put in the
    drafter's facts (an invoice number such as FS-2026-1190), which aren't contact details."""
    text = f"{draft.subject}\n{draft.body}"
    for token in allowed:
        text = text.replace(token, "")
    problems = [f"contains {what}" for what, pattern in _BANNED if pattern.search(text)]
    if len(draft.body.split()) > MAX_DRAFT_WORDS:
        problems.append(f"longer than {MAX_DRAFT_WORDS} words")
    return problems


def _facts_for_drafter(step: Step, vendor: VendorProfile, baselines: BaselineStore) -> str:
    return (
        "FACTS FROM OUR RECORDS (computed by our code):\n"
        f"- Our business: {baselines.organization.name}\n"
        f"- The vendor: {vendor.name}\n"
        f"- What we received: {step.issue}\n"
        "- We will not act on it until the vendor confirms through a channel we already have."
    )


def draft_for(
    step: Step, client: EmberClient, baselines: BaselineStore, *, item_id: str
) -> tuple[Draft | None, CallRecord | None, str | None]:
    """(draft, ledger record, note). Only for steps that name a vendor with an address on file."""
    vendor = baselines.vendor_by_id(step.draft_vendor_id)
    if vendor is None or not vendor.contact_email or not step.issue:
        return None, None, None
    items = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": _facts_for_drafter(step, vendor, baselines)}
            ],
        }
    ]
    try:
        out = run_agent(
            client,
            DRAFTER,
            input_items=items,
            input_kind=InputKind.TEXT,
            item_id=item_id,
        )
    except BudgetExceeded:
        return None, None, "no draft: the budget refused the call"
    if out.value is None:
        return None, out.record, f"no draft: {out.error}"
    # Code wrote every digit-bearing token in the issue (an invoice number): those may appear.
    allowed = tuple(re.findall(r"[\w-]*\d[\w-]*", step.issue))
    problems = draft_problems(out.value, allowed)
    if problems:
        return None, out.record, f"draft rejected by code checks ({', '.join(problems)})"
    return Draft(vendor.contact_email, out.value.subject, out.value.body), out.record, None
