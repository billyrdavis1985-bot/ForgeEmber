"""A fake Meta transport that answers like a *correct* Ember would, for the generated fixtures.

It lets us test that, given correct proposals, code + verification + policy reproduce the
labels in fixtures/manifest.json exactly. Quotes are real substrings of the fixture text, so
verification runs for real. Nothing here touches the network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from conftest import function_call_payload, responses_payload
from forgeember.ember.client import TransportResponse
from forgeember.envelope import Envelope
from forgeember.fixturegen import INVOICES, VOICEMAILS, spaced

# Phrases a correct model would quote as manipulation signals, by attack type.
SIGNAL_PHRASES = {
    "bank_detail_change": (
        "bank_change_request",
        ["our bank details have changed", "update your records"],
    ),
    "executive_impersonation": ("secrecy", ["Keep this between us for now"]),
    "reply_to_diversion": ("urgency", ["please confirm today that payment has been sent"]),
    "prompt_injection": ("other", ["ignore previous instructions"]),
    "vendor_impersonation": ("urgency", ["please settle the balance today"]),
}
VERDICTS = {
    "bank_detail_change": "likely_fraud",
    "executive_impersonation": "likely_fraud",
    "prompt_injection": "likely_fraud",
    "reply_to_diversion": "suspicious",
    "vendor_impersonation": "likely_fraud",
}
# Who each email claims to be from, as a careful reader quotes it (identity specialist).
CLAIMS = {
    "em-007": ("Brightwater Supplies", "Thanks,\nBrightwater Supplies"),
    "em-008": ("Fennimore Stationery", "This is the accounts team at Fennimore Stationery."),
}


class IdealEmber:
    def __init__(
        self, item: Mapping[str, Any], env: Envelope, *, usage: dict[str, int] | None = None
    ):
        self.item, self.env = item, env
        self.usage = usage or {
            "input_tokens": 1800,
            "cached": 1200,
            "output_tokens": 260,
            "reasoning": 90,
        }
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    # ------------------------------------------------------------------ transport
    def post_json(self, path: str, body: Mapping[str, Any]) -> TransportResponse:
        self.calls.append((path, body))
        if path == "/responses/input_tokens":
            return TransportResponse(200, {"object": "response.input_tokens", "input_tokens": 1500})
        name = body["text"]["format"]["name"]
        answer = getattr(self, name)(body)
        if "function_calls" in answer:  # a tool round: ask code to run lookups
            return TransportResponse(200, function_call_payload(answer["function_calls"]))
        u = self.usage
        return TransportResponse(
            200,
            responses_payload(
                text=json.dumps(answer),
                input_tokens=u["input_tokens"],
                cached=u["cached"],
                output_tokens=u["output_tokens"],
                reasoning=u["reasoning"],
            ),
        )

    # ------------------------------------------------------------------ helpers
    @property
    def attack(self) -> str | None:
        return self.item.get("attack")

    def signals(self) -> list[dict[str, str]]:
        if self.attack not in SIGNAL_PHRASES:
            return []
        kind, phrases = SIGNAL_PHRASES[self.attack]
        for part in self.env.parts:
            for phrase in phrases:
                if part.text and phrase in part.text:
                    return [
                        {
                            "type": kind,
                            "part": part.name,
                            "quote": phrase,
                            "explanation": "manipulation signal",
                        }
                    ]
        return []

    def document_part(self) -> str | None:
        return next((p.name for p in self.env.parts if p.kind.value in ("pdf", "image")), None)

    # ------------------------------------------------------------------ agents
    def triage_result(self, body: Mapping[str, Any]) -> dict[str, Any]:
        signals = self.signals()
        return {
            "category": self.item["expected"]["category"],
            "summary": self.item["scenario"],
            "needs_document_extraction": self.document_part() is not None,
            "risk_signals": signals,
            "escalate": bool(signals),
        }

    def invoice_extraction(self, body: Mapping[str, Any]) -> dict[str, Any]:
        key = {"em-005": "em-005-attachment", "em-007": "em-007-attachment"}.get(
            self.item["id"], self.item["id"]
        )
        inv = INVOICES[key]
        part = self.document_part()
        evidence = [
            {"field": "vendor_name", "part": part, "quote": inv.vendor},
            {"field": "invoice_number", "part": part, "quote": f"Invoice number: {inv.number}"},
            {"field": "total", "part": part, "quote": f"Total due: GBP {inv.total}"},
            {"field": "iban", "part": part, "quote": f"IBAN: {spaced(inv.iban)}"},
        ]
        if inv.notice:
            evidence.append(
                {
                    "field": "bank_change_notice",
                    "part": part,
                    "quote": "our bank details have changed",
                }
            )
        return {
            "is_invoice": True,
            "vendor_name": inv.vendor,
            "vendor_email": inv.vendor_email,
            "invoice_number": inv.number,
            "invoice_date": inv.date,
            "due_date": inv.due,
            "currency": "GBP",
            "line_items": [
                {"description": d, "quantity": q, "unit_price": u, "amount": a}
                for d, q, u, a in inv.lines
            ],
            "subtotal": inv.subtotal,
            "tax": inv.tax,
            "total": inv.total,
            "bank": {
                "account_name": inv.account_name,
                "iban": spaced(inv.iban),
                "account_number": None,
                "routing_number": None,
                "swift_bic": None,
            },
            "claims_bank_change": inv.notice is not None,
            "evidence": evidence,
        }

    def voicemail_note(self, body: Mapping[str, Any]) -> dict[str, Any]:
        vm = VOICEMAILS[self.item["id"]]
        bank = None
        if vm.account_number:
            bank = {
                "account_name": None,
                "iban": None,
                "account_number": vm.account_number,
                "routing_number": vm.routing_number,
                "swift_bic": None,
            }
        return {
            "transcript": vm.transcript(),
            "caller_name": vm.caller_name,
            "caller_organization": vm.caller_organization,
            "callback_number": vm.callback_number,
            "request": vm.request,
            "asks_for_payment": vm.asks_for_payment,
            "amount": vm.amount,
            "claims_bank_change": vm.claims_bank_change,
            "bank": bank,
        }

    def identity_assessment(self, body: Mapping[str, Any]) -> dict[str, Any]:
        claim = CLAIMS.get(self.item["id"])
        outputs = [
            json.loads(i["output"])
            for i in body["input"]
            if isinstance(i, Mapping) and i.get("type") == "function_call_output"
        ]
        if claim and not outputs:  # round 1: look the claimed sender up
            return {"function_calls": [("call_1", "lookup_vendor", {"query": claim[0]})]}
        found = next((o for o in outputs if o.get("found")), None)
        domain = self.env.headers.from_domain if self.env.headers else ""
        return {
            "claimed_sender": claim[0] if claim else None,
            "claim_part": "body" if claim else None,
            "claim_quote": claim[1] if claim else None,
            "vendor_id": found["vendor_id"] if found else None,
            "consistent_with_records": not found or domain in found["email_domains"],
            "explanation": "per fixture label",
        }

    def draft_message(self, body: Mapping[str, Any]) -> dict[str, Any]:
        facts = body["input"][0]["content"][0]["text"]
        vendor = re.search(r"- The vendor: (.+)", facts).group(1)
        issue = re.search(r"- What we received: (.+)", facts).group(1)
        return {
            "subject": "Please confirm a recent request",
            "body": f"Hello {vendor} accounts team, we received {issue}. Before we act on it, "
            "please confirm whether it came from you, by phone on the number we have for you or "
            "by replying from your usual address. We won't make any change until we hear from "
            "you. Ashgrove Bakery",
        }

    def risk_assessment(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "verdict": VERDICTS.get(self.attack or "", "benign"),
            "signals": self.signals(),
            "rationale": "per fixture label",
        }

    def skeptic_view(self, body: Mapping[str, Any]) -> dict[str, Any]:
        note = json.dumps(body["input"])
        other = re.search(r"THE OTHER REVIEWER'S VERDICT: (\w+)", note).group(1)
        return {
            "counter_verdict": "benign" if other != "benign" else "suspicious",
            "argument": "The strongest innocent explanation.",
            "evidence_that_would_change_my_mind": "Call the vendor on the number on file.",
        }
