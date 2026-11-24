"""Read-only lookups Ember may call (function tools). Code runs them; Ember only asks.

Each tool reads our records and returns JSON. None can write, send or pay, and none reveals
bank details: code compares accounts itself (baseline.py). A tool call's arguments are
model output, so they are validated and bounded like any other untrusted input.

Every lookup is kept in a trail. ``returned_vendor_ids`` lets verification check that a
vendor id in Ember's final answer really came back from a lookup in that conversation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from forgeember.baseline import BaselineStore, VendorProfile, _norm_name

MAX_QUERY_CHARS = 200
MIN_PARTIAL_CHARS = 4  # shortest partial name worth matching

LOOKUP_VENDOR = {
    "type": "function",
    "name": "lookup_vendor",
    "description": (
        "Look up one of our vendors by company name, alias or email domain. Read-only. "
        "Returns the vendor's id, names, email domains and usual invoice range, or found=false."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A company name or alias as written, or an email domain.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    output: dict[str, Any]


def _vendor_view(v: VendorProfile) -> dict[str, Any]:
    return {
        "found": True,
        "vendor_id": v.vendor_id,
        "name": v.name,
        "aliases": list(v.aliases),
        "email_domains": sorted(v.email_domains),
        "currency": v.currency,
        "usual_invoice_total": f"{v.typical_total_min}-{v.typical_total_max}",
    }


@dataclass
class VendorTools:
    """The identity specialist's toolbox, over one baseline store."""

    baselines: BaselineStore
    trail: list[ToolCall] = field(default_factory=list)

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [LOOKUP_VENDOR]

    @property
    def returned_vendor_ids(self) -> set[str]:
        return {str(c.output["vendor_id"]) for c in self.trail if c.output.get("found")}

    def lookup_vendor(self, query: str) -> dict[str, Any]:
        q = query.strip()
        vendor = self.baselines.vendor_by_domain(q.lower().lstrip("@")) or (
            self.baselines.vendor_by_name(q)
        )
        if vendor is None:  # a name inside a longer phrase: "Fennimore Stationery accounts team"
            target = f" {_norm_name(q)} "
            for v in self.baselines.vendors:
                if any(f" {_norm_name(n)} " in target for n in (v.name, *v.aliases)):
                    vendor = v
                    break
        if vendor is None and len(_norm_name(q)) >= MIN_PARTIAL_CHARS:
            # whole words of a name ("Fennimore"): only when exactly one vendor fits
            words = f" {_norm_name(q)} "
            hits = [
                v
                for v in self.baselines.vendors
                if any(words in f" {_norm_name(n)} " for n in (v.name, *v.aliases))
            ]
            if len(hits) == 1:
                vendor = hits[0]
            elif hits:
                return {"found": False, "query": q, "candidates": sorted(v.name for v in hits)}
        return _vendor_view(vendor) if vendor else {"found": False, "query": q}

    def run(self, name: str, raw_arguments: str) -> dict[str, Any]:
        """Execute one call from the model. Bad input gets an error result, never an exception."""
        try:
            args = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            out: dict[str, Any] = {"error": "arguments must be a JSON object"}
            args = {}
        elif name != "lookup_vendor":
            out = {"error": f"unknown tool {name!r}"}
        elif not isinstance(args.get("query"), str) or not args["query"].strip():
            out = {"error": "query must be a non-empty string"}
        elif len(args["query"]) > MAX_QUERY_CHARS:
            out = {"error": f"query longer than {MAX_QUERY_CHARS} characters"}
        else:
            out = self.lookup_vendor(args["query"])
        self.trail.append(ToolCall(name=name, arguments=args, output=out))
        return out
