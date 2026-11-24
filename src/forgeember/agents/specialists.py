"""Specialists. Each returns evidence-cited findings as strict structured output.

* document: invoice and receipt fields and bank details, quoted from the source. Code checks the
  arithmetic, the IBAN checksum and the bank baseline.
* voicemail: what a recording says (transcript, caller, request, any account read out). Runs
  on every audio part, like document extraction on every document. Code compares an account
  read out with the vendor's account on file.
* risk: manipulation in language and context (the identity and payment-change specialist).
  Low effort: when code already found a red flag the decision is HIGH regardless, so the
  risk read only enriches the explanation on the hold.
"""

from __future__ import annotations

from forgeember.agents.base import AgentSpec
from forgeember.ember.prompts import load_prompt
from forgeember.schemas import InvoiceExtraction, RiskAssessment, VoicemailNote

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
