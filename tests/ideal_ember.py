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

from conftest import responses_payload
from forgeember.ember.client import TransportResponse
from forgeember.envelope import Envelope
from forgeember.fixturegen import INVOICES, spaced

# Phrases a correct model would quote as manipulation signals, by attack type.
SIGNAL_PHRASES = {
    "bank_detail_change": (
        "bank_change_request",
        ["our bank details have changed", "update your records"],
    ),
    "executive_impersonation": ("secrecy", ["Keep this between us for now"]),
    "reply_to_diversion": ("urgency", ["please confirm today that payment has been sent"]),
    "prompt_injection": ("other", ["ignore previous instructions"]),
}
VERDICTS = {
    "bank_detail_change": "likely_fraud",
    "executive_impersonation": "likely_fraud",
    "prompt_injection": "likely_fraud",
    "reply_to_diversion": "suspicious",
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
