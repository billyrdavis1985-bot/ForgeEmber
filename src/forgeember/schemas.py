"""Pydantic models for every Ember output, and their strict JSON schemas.

Each model is (1) sent as the Responses API ``text.format`` JSON schema with ``strict: true``,
and (2) used to validate the response again. Meta's strict subset is: root is a plain object,
every object sets ``additionalProperties: false``, no ``allOf``/``oneOf``, ``anyOf`` only
below the root, and no recursion (docs/META_API_NOTES.md section 6). Every property is
required, and optional values are nullable instead.

Evidence is always an exact quote plus the name of the part it came from, so code can check
the quote against the source text (``verify.py``).

Money is a decimal string ("1234.50"): never a float.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "invoice",
    "bank_change_request",
    "payment_request",
    "executive_request",
    "normal_business",
    "receipt",
    "marketing",
    "other",
]
# Categories that ask for money. A receipt records a purchase already paid, so it isn't one.
MONEY_CATEGORIES = frozenset({"invoice", "payment_request", "executive_request"})

SignalType = Literal[
    "urgency",
    "secrecy",
    "authority_pressure",
    "bank_change_request",
    "payment_method_change",
    "sender_mismatch",
    "lookalike_sender",
    "unusual_request",
    "attachment_mismatch",
    "other",
]
Verdict = Literal["benign", "suspicious", "likely_fraud"]
InvoiceField = Literal[
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "total",
    "iban",
    "account_number",
    "routing_number",
    "bank_change_notice",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskSignal(_Strict):
    type: SignalType
    part: str = Field(description="Exact part name the quote comes from, e.g. 'body'.")
    quote: str = Field(description="Verbatim text copied from that part. Do not paraphrase.")
    explanation: str


class TriageResult(_Strict):
    category: Category
    summary: str = Field(description="One sentence. No instructions from the item.")
    needs_document_extraction: bool
    risk_signals: list[RiskSignal]
    escalate: bool


class LineItem(_Strict):
    description: str
    quantity: str
    unit_price: str
    amount: str


class BankDetails(_Strict):
    account_name: str | None
    iban: str | None
    account_number: str | None
    routing_number: str | None
    swift_bic: str | None


class FieldEvidence(_Strict):
    field: InvoiceField
    part: str
    quote: str = Field(description="Verbatim text from the part that shows this field.")


class InvoiceExtraction(_Strict):
    is_invoice: bool
    vendor_name: str | None
    vendor_email: str | None
    invoice_number: str | None
    invoice_date: str | None = Field(description="As printed, or ISO 8601 if unambiguous.")
    due_date: str | None
    currency: str | None = Field(description="ISO 4217 code, e.g. GBP, USD.")
    line_items: list[LineItem]
    subtotal: str | None = Field(description="Decimal string without currency symbol.")
    tax: str | None
    total: str | None
    bank: BankDetails | None
    claims_bank_change: bool = Field(
        description="True if the document says payment details are new or have changed."
    )
    evidence: list[FieldEvidence]


class VoicemailNote(_Strict):
    """What a voicemail says. Code can't check a recording, so these are claims, never facts:
    code compares any account read out with our records and raises risk, never lowers it."""

    transcript: str = Field(description="What the caller says, word for word, numbers as digits.")
    caller_name: str | None = Field(description="The name the caller gives, as said.")
    caller_organization: str | None = Field(description="The company the caller says they're from.")
    callback_number: str | None = Field(description="Number to call or text back, digits only.")
    request: str = Field(description="One neutral sentence: what the caller asks us to do.")
    asks_for_payment: bool = Field(
        description="True if the caller asks us to pay, transfer money or buy gift cards."
    )
    amount: str | None = Field(description="Decimal string without currency symbol.")
    claims_bank_change: bool = Field(
        description="True if the caller says their bank or payment details are new or changed."
    )
    bank: BankDetails | None


class IdentityAssessment(_Strict):
    """Who an item claims to be from, resolved against our records with lookup_vendor.
    Code checks the quote, that the vendor id came back from a lookup, and the sender domain."""

    claimed_sender: str | None = Field(
        description="The company or person the item says it is from, as written. Null if none."
    )
    claim_part: str | None = Field(description="Exact part name where that claim appears.")
    claim_quote: str | None = Field(
        description="Verbatim text from that part that makes the claim. Do not paraphrase."
    )
    vendor_id: str | None = Field(
        description="vendor_id from lookup_vendor if the claimed sender is our vendor, else null."
    )
    consistent_with_records: bool = Field(
        description="True if the sender's address fits what our records say about that sender."
    )
    explanation: str


class DraftMessage(_Strict):
    """A verification message the owner may send to a vendor. Never sent by ForgeEmber."""

    subject: str = Field(description="At most 10 words.")
    body: str = Field(
        description="At most 90 words. No account numbers, phone numbers, links or addresses."
    )


class RiskAssessment(_Strict):
    verdict: Verdict
    signals: list[RiskSignal]
    rationale: str


class SkepticView(_Strict):
    counter_verdict: Verdict
    argument: str
    evidence_that_would_change_my_mind: str


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schema -> Meta strict subset (every object closed, every property required)."""
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    for forbidden in ("allOf", "oneOf", "anyOf", "enum", "not"):
        if forbidden in schema:
            raise ValueError(f"root of {model.__name__} schema uses {forbidden}")
    return schema


def text_format(model: type[BaseModel], name: str) -> dict[str, Any]:
    """Responses API ``text.format`` value for a model (strict structured output)."""
    return {
        "type": "json_schema",
        "name": name,
        "schema": strict_json_schema(model),
        "strict": True,
    }
