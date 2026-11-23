"""Deterministic verification of Ember findings. Nothing reaches policy as trusted unless it passes.

Checks:
* every quoted piece of evidence appears verbatim in the named part's text (whitespace-normalized);
* extracted key values (total, IBAN, invoice number) actually occur in the source text;
* invoice arithmetic adds up (line amounts, subtotal + tax = total);
* the IBAN passes its mod-97 checksum.

Evidence from parts with no text (images, audio, video) is marked UNVERIFIABLE. It's never
silently accepted as PASS.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from forgeember.baseline import iban_is_valid, normalize_iban, parse_amount
from forgeember.envelope import Envelope
from forgeember.schemas import InvoiceExtraction, RiskSignal

TOLERANCE = Decimal("0.01")


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: CheckStatus
    detail: str = ""


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for fancy, plain in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"')):
        text = text.replace(fancy, plain)
    return " ".join(text.split())


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", _norm(text))


def quote_status(env: Envelope, part_name: str, quote: str) -> CheckStatus:
    part = env.part(part_name)
    if part is None or not quote.strip():
        return CheckStatus.FAIL
    if part.text is None:
        return CheckStatus.UNVERIFIABLE
    if _norm(quote) in _norm(part.text) or _squash(quote) in _squash(part.text):
        return CheckStatus.PASS
    return CheckStatus.FAIL


def all_text(env: Envelope) -> str | None:
    texts = [p.text for p in env.parts if p.text]
    return "\n".join(texts) if texts else None


def _amounts_in(text: str) -> set[Decimal]:
    found = set()
    for raw in re.findall(r"\d[\d,]*\.?\d*", text):
        value = parse_amount(raw)
        if value is not None:
            found.add(value)
    return found


def verify_signals(env: Envelope, signals: Iterable[RiskSignal], prefix: str) -> list[Check]:
    return [
        Check(f"{prefix}.quote[{i}]:{s.type}", quote_status(env, s.part, s.quote), s.quote[:80])
        for i, s in enumerate(signals)
    ]


def verify_invoice(env: Envelope, ext: InvoiceExtraction) -> list[Check]:
    checks: list[Check] = []
    for i, ev in enumerate(ext.evidence):
        checks.append(
            Check(
                f"invoice.evidence[{i}]:{ev.field}",
                quote_status(env, ev.part, ev.quote),
                ev.quote[:80],
            )
        )

    source = all_text(env)
    key_values: dict[str, str | None] = {
        "total": ext.total,
        "invoice_number": ext.invoice_number,
        "iban": ext.bank.iban if ext.bank else None,
        "account_number": ext.bank.account_number if ext.bank else None,
    }
    for field_name, value in key_values.items():
        if value is None:
            continue
        name = f"invoice.in_source:{field_name}"
        if source is None:
            checks.append(Check(name, CheckStatus.UNVERIFIABLE, "no text layer"))
        elif field_name == "total":
            amount = parse_amount(value)
            ok = amount is not None and amount in _amounts_in(source)
            checks.append(Check(name, CheckStatus.PASS if ok else CheckStatus.FAIL, value))
        else:
            ok = _squash(value).upper() in _squash(source).upper()
            checks.append(Check(name, CheckStatus.PASS if ok else CheckStatus.FAIL, value))

    if ext.bank and ext.bank.iban:
        valid = iban_is_valid(ext.bank.iban)
        checks.append(
            Check(
                "invoice.iban_checksum",
                CheckStatus.PASS if valid else CheckStatus.FAIL,
                normalize_iban(ext.bank.iban),
            )
        )

    checks.extend(_math_checks(ext))
    return checks


def _math_checks(ext: InvoiceExtraction) -> list[Check]:
    checks: list[Check] = []
    line_sum = Decimal(0)
    lines_ok = True
    for i, line in enumerate(ext.line_items):
        qty, price, amount = (
            parse_amount(x) for x in (line.quantity, line.unit_price, line.amount)
        )
        if None in (qty, price, amount):
            checks.append(Check(f"invoice.math.line[{i}]", CheckStatus.FAIL, "unparseable"))
            lines_ok = False
            continue
        if abs(qty * price - amount) > TOLERANCE:  # type: ignore[operator]
            checks.append(
                Check(f"invoice.math.line[{i}]", CheckStatus.FAIL, f"{qty} x {price} != {amount}")
            )
        line_sum += amount  # type: ignore[operator]
    subtotal, tax, total = (parse_amount(x) for x in (ext.subtotal, ext.tax, ext.total))
    if ext.line_items and lines_ok and subtotal is not None:
        ok = abs(line_sum - subtotal) <= TOLERANCE
        checks.append(
            Check(
                "invoice.math.subtotal",
                CheckStatus.PASS if ok else CheckStatus.FAIL,
                f"lines {line_sum} vs subtotal {subtotal}",
            )
        )
    if subtotal is not None and total is not None:
        ok = abs(subtotal + (tax or Decimal(0)) - total) <= TOLERANCE
        checks.append(
            Check(
                "invoice.math.total",
                CheckStatus.PASS if ok else CheckStatus.FAIL,
                f"{subtotal} + {tax or 0} vs total {total}",
            )
        )
    elif ext.total is not None and total is None:
        checks.append(Check("invoice.math.total", CheckStatus.FAIL, "unparseable total"))
    return checks
