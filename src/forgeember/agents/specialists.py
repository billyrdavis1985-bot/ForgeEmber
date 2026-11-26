"""Specialists. Each returns evidence-cited findings as strict structured output.

* document: invoice and receipt fields and bank details, quoted from the source. Code checks the
  arithmetic, the IBAN checksum and the bank baseline.
* voicemail: what a recording says (transcript, caller, request, any account read out). Runs
  on every audio part, like document extraction on every document. Code compares an account
  read out with the vendor's account on file.
* identity: who an email claims to be from, resolved with the read-only lookup_vendor tool
  (function calling). Code checks the quoted claim, that the vendor id came back from a
  lookup, and whether the sender's domain is on file for that vendor.
* handoff drafter: a short verification message to a vendor, written from facts code
  produced (it never sees the message under review, so nothing in it can steer the draft).
  Code checks the draft and picks the recipient; ForgeEmber never sends it.
* video: what a screen recording shows (on-screen text, whose payment details change, the
  details entered). Runs on every video part; code compares any account shown with the
  one on file.
* risk: manipulation in language and context (the identity and payment-change specialist).
  Low effort: when code already found a red flag the decision is HIGH regardless, so the
  risk read only enriches the explanation on the hold.
"""

from __future__ import annotations

from forgeember.agents.base import AgentSpec
from forgeember.ember.prompts import load_prompt
from forgeember.schemas import (
    DraftMessage,
    IdentityAssessment,
    InvoiceExtraction,
    RiskAssessment,
    VideoNote,
    VoicemailNote,
)

DOCUMENT = AgentSpec(
    role="document",
    prompt=load_prompt("document", 2),
    model=InvoiceExtraction,
    schema_name="invoice_extraction",
    effort="low",
    max_output_tokens=4096,
)

RISK = AgentSpec(
    role="risk",
    prompt=load_prompt("risk", 1),
    model=RiskAssessment,
    schema_name="risk_assessment",
    effort="low",
    max_output_tokens=2048,
)

VOICEMAIL = AgentSpec(
    role="voicemail",
    prompt=load_prompt("voicemail", 1),
    model=VoicemailNote,
    schema_name="voicemail_note",
    effort="low",
    max_output_tokens=2048,
)

IDENTITY = AgentSpec(
    role="identity",
    prompt=load_prompt("identity", 1),
    model=IdentityAssessment,
    schema_name="identity_assessment",
    effort="low",
    max_output_tokens=2048,
)

DRAFTER = AgentSpec(
    role="handoff",
    prompt=load_prompt("draft", 1),
    model=DraftMessage,
    schema_name="draft_message",
    effort="minimal",
    max_output_tokens=1024,
)

VIDEO = AgentSpec(
    role="video",
    prompt=load_prompt("video", 1),
    model=VideoNote,
    schema_name="video_note",
    effort="low",
    max_output_tokens=2048,
)
