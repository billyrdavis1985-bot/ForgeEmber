"""The sole decision authority. Ember proposes; this module decides.

Tier table (an invariant, see docs/INTENT.md):
  T0 automatic: file, tag, route a note
  T1 notify the operator
  T2 HOLD for human approval: payments, bank-detail changes, outside sends, deletions
Ember can never execute a T2 action. Unknown action kinds default to T2.

``decide`` takes only ``Facts``, values that code computed or verified. Ember's own verdict
counts only when it's backed by verified quotes. Without that it can raise risk to MEDIUM at
most, and it can never lower a risk that code established.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from forgeember.baseline import AmountStatus, BankStatus
from forgeember.schemas import MONEY_CATEGORIES


class Tier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"


ACTION_TIERS: dict[str, Tier] = {
    "file": Tier.T0,
    "tag": Tier.T0,
    "route_note": Tier.T0,
    "notify_operator": Tier.T1,
    "pay": Tier.T2,
    "change_bank_details": Tier.T2,
    "send_external": Tier.T2,
    "delete": Tier.T2,
}

DISPOSITION = {Tier.T0: "execute", Tier.T1: "notify", Tier.T2: "hold"}


def tier_for(action_kind: str) -> Tier:
    """Unknown actions are treated as the riskiest tier."""
    return ACTION_TIERS.get(action_kind, Tier.T2)


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_RANK = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}


@dataclass(frozen=True, slots=True)
class ActionDecision:
    kind: str
    tier: Tier
    disposition: str
    reason: str

    def __post_init__(self) -> None:
        if self.tier is not tier_for(self.kind):
            raise ValueError(f"action {self.kind} must be tier {tier_for(self.kind)}")
        if self.disposition != DISPOSITION[self.tier]:
            raise ValueError(
                f"{self.tier} actions are '{DISPOSITION[self.tier]}', never '{self.disposition}'"
            )


def action(kind: str, reason: str) -> ActionDecision:
    tier = tier_for(kind)
    return ActionDecision(kind=kind, tier=tier, disposition=DISPOSITION[tier], reason=reason)


@dataclass(frozen=True, slots=True)
class Facts:
    """Everything policy may look at. Produced by code (pipeline + verify), never by Ember alone."""

    category: str  # Ember's proposed route (a proposal, not a decision)
    screening_complete: bool = True
    vendor_known: bool | None = None  # None: not a vendor/money item
    bank_status: BankStatus | None = None
    amount_status: AmountStatus | None = None
    iban_valid: bool | None = None
    math_ok: bool | None = None
    key_fields_unconfirmed: bool = False  # extracted payment fields not found in the source
    evidence_unverifiable: bool = False  # evidence came from image/audio with no text layer
    claims_bank_change: bool = False
    lookalike_domain: bool = False
    executive_impersonation: bool = False
    reply_to_mismatch: bool = False
    attachment_mime_mismatch: bool = False
    injection_attempt: bool = False  # text addressed to automated reviewers (code regex)
    ember_verdict: str | None = None  # after the skeptic, if it ran
    verified_signals: int = 0  # Ember risk signals whose quotes code verified
    notes: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class Decision:
    risk: Risk
    reasons: tuple[str, ...]
    actions: tuple[ActionDecision, ...]

    @property
    def held(self) -> tuple[ActionDecision, ...]:
        return tuple(a for a in self.actions if a.disposition == "hold")


def assess(f: Facts) -> tuple[Risk, list[str]]:
    reasons: list[tuple[Risk, str]] = []

    def add(level: Risk, why: str) -> None:
        reasons.append((level, why))

    if not f.screening_complete:
        add(Risk.MEDIUM, "screening did not finish (budget stop or invalid model output)")
    if f.bank_status is BankStatus.CHANGED:
        add(Risk.HIGH, "bank account differs from every account on file for this vendor")
    if f.iban_valid is False:
        add(Risk.HIGH, "IBAN fails its checksum")
    if f.math_ok is False:
        add(Risk.HIGH, "invoice arithmetic does not add up")
    if f.key_fields_unconfirmed:
        add(Risk.HIGH, "extracted payment details were not found in the document text")
    if f.lookalike_domain:
        add(Risk.HIGH, "sender domain imitates a known vendor's domain")
    if f.executive_impersonation:
        add(Risk.HIGH, "sender uses an executive's name from an outside address")
    if f.attachment_mime_mismatch:
        add(Risk.HIGH, "attachment's declared type does not match its content")
    if f.injection_attempt:
        add(Risk.HIGH, "item contains instructions aimed at automated reviewers (prompt injection)")
    if f.claims_bank_change or f.category == "bank_change_request":
        add(Risk.HIGH, "request to change payment details: verify by phone before any change")
    if f.ember_verdict == "likely_fraud":
        if f.verified_signals:
            add(
                Risk.HIGH, f"Ember flagged likely fraud with {f.verified_signals} verified quote(s)"
            )
        else:
            add(Risk.MEDIUM, "Ember flagged likely fraud, but no quote could be verified")
    elif f.ember_verdict == "suspicious":
        add(Risk.MEDIUM if f.verified_signals else Risk.LOW, "Ember found suspicious language")
    if f.category in MONEY_CATEGORIES and f.vendor_known is False:
        add(Risk.MEDIUM, "payment requested by a vendor with no baseline")
    if f.amount_status is AmountStatus.UNUSUAL:
        add(Risk.MEDIUM, "amount is outside this vendor's usual range")
    if f.amount_status is AmountStatus.UNPARSEABLE and f.category in MONEY_CATEGORIES:
        add(Risk.MEDIUM, "amount could not be read")
    if f.evidence_unverifiable:
        add(Risk.MEDIUM, "evidence comes from an image or recording that code cannot check")
    if f.reply_to_mismatch:
        add(Risk.MEDIUM, "Reply-To points to a different domain than the sender")

    risk = max((lvl for lvl, _ in reasons), key=_RANK.__getitem__, default=Risk.LOW)
    ordered = [
        why for lvl, why in sorted(reasons, key=lambda r: -_RANK[r[0]]) if lvl is not Risk.LOW
    ]
    return risk, ordered


def decide(f: Facts) -> Decision:
    risk, reasons = assess(f)
    acts = [action("file", f"route: {f.category}"), action("tag", f"risk: {risk.value}")]
    if f.category in MONEY_CATEGORIES:
        acts.append(action("pay", f"payment needs approval (risk {risk.value})"))
    if f.claims_bank_change or f.category == "bank_change_request":
        acts.append(action("change_bank_details", "bank-detail changes always need approval"))
    if risk is not Risk.LOW:
        acts.append(action("notify_operator", "; ".join(reasons[:3])))
    elif f.category == "normal_business":
        acts.append(action("route_note", "normal business mail"))
    return Decision(risk=risk, reasons=tuple(reasons), actions=tuple(acts))
