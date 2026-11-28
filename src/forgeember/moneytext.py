"""Money in a message's own text, as code reads it: payment details, a demand for payment, and
a claim that payment details have changed.

This is the route's backstop. Ember's triage decides whether an email is a payment request,
and policy holds a payment only on a money route, so an email that triage misfiled as
ordinary mail would get no payment hold at all. Code reads the text itself and raises the
route (pipeline.Screener._money_floor). It never lowers one.

Read the way a person would: invisible characters dropped and look-alike letters read as
Latin, so "GB91<ZWSP>ZZTB..." and "overdue" spelled with a Cyrillic o still count.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from forgeember.baseline import iban_is_valid, skeleton
from forgeember.envelope import strip_invisible

# An IBAN: country, check digits, then 11-30 letters or digits, spaced in groups or not. It
# counts when its checksum holds or the text labels it "IBAN" just before (a failed checksum
# on a labelled IBAN is still payment details, and policy flags the checksum separately).
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
_IBAN_LABEL_WINDOW = 24
# A labelled account number: "Account number: 4417 7620", "Sort code 20-45-77", "ABA 0260...".
_ACCOUNT = re.compile(
    r"\b(?:account\s+(?:number|no\.?)|acct\.?\s*(?:no\.?|#)?|sort\s+code|routing\s+(?:number|no\.?)"
    r"|aba)\s*[:#]?\s*\d[\d -]{5,}"
)
_AMOUNT = re.compile(r"(?:\b(?:gbp|usd|eur)\s?|[£$€]\s?)(\d[\d,]*(?:\.\d{1,2})?)")
# "Invoice FS-2026-1182", "invoice no. 4411": a reference has a digit ("invoice from" is not one).
_INVOICE_REF = re.compile(
    r"\binvoice\s+(?:no\.?\s*|number:?\s*|#\s*)?(?=[a-z0-9/-]*\d)[a-z0-9][a-z0-9/-]{3,}"
)
# Asking for money, not reporting it: "we've paid", "payment received" and "happy to pay in
# the shop" don't match.
_DEMAND = re.compile(
    r"\b(?:please\s+(?:pay|settle|send|transfer|remit|wire|arrange\s+payment"
    r"|confirm\s+(?:today\s+)?(?:that\s+)?payment)"
    r"|pay\s+(?:it|this|the|by|today|now|within|immediately)"
    r"|arrange\s+payment|make\s+(?:a|the)\s+payment|settle\s+(?:the|your|this|it)"
    r"|(?:send|transfer|wire)\s+(?:gbp|usd|eur|[£$€])"
    r"|overdue|unpaid|outstanding\s+balance|(?:amount|balance|total)\s+due|payment\s+(?:is\s+)?due)"
    r"\b"
)
# "Amount due: GBP 0.00" on a paid receipt is not a demand.
_ZERO_DUE = re.compile(
    r"\b(?:amount|balance|total)\s+due\b\W*(?:(?:gbp|usd|eur)\s?|[£$€]\s?)?0+(?:\.0+)?(?![.,]?\d)"
)
# A claim that the details are new or changed. Counted only alongside payment details.
_CHANGE = re.compile(
    r"\b(?:new\s+(?:bank|banking)\b"
    # "our new account", never "your new account": a sign-up email isn't a bank change
    r"|(?:our|their|my)\s+new\s+(?:account|provider|remittance|payment\s+details|details)"
    r"|(?:bank|account|banking|payment|remittance)\s+details\s+(?:have\s+|has\s+)?changed"
    r"|changed\s+(?:our|the|its)\s+(?:bank|account|banking)"
    r"|moved\s+(?:our|its)\s+(?:bank|banking|account)"
    r"|update\s+your\s+records|updated?\s+(?:bank|remittance|payment)\s+details)\b"
)


@dataclass(frozen=True, slots=True)
class MoneyText:
    payment_details: bool = False  # an IBAN or a labelled account number
    payment_demand: bool = False  # asks for payment of an amount or an invoice
    bank_change: bool = False  # payment details, said to be new or changed

    @property
    def asks_for_money(self) -> bool:
        return self.payment_details or self.payment_demand


def _plain(text: str) -> str:
    return skeleton(strip_invisible(text))


def _has_iban(plain: str) -> bool:
    upper = plain.upper()
    for m in _IBAN.finditer(upper):
        if "IBAN" in upper[max(0, m.start() - _IBAN_LABEL_WINDOW) : m.start()]:
            return True
        # Unlabelled: the checksum must hold, for the whole match or for the match cut at a
        # space (the pattern can run on into the next word), with digits enough for an account.
        groups = m.group(0).split(" ")
        for n in range(len(groups), 0, -1):
            candidate = "".join(groups[:n])
            if sum(c.isdigit() for c in candidate[4:]) >= 6 and iban_is_valid(candidate):
                return True
    return False


def _has_amount(plain: str) -> bool:
    for m in _AMOUNT.finditer(plain):
        try:
            if float(m.group(1).replace(",", "")) > 0:
                return True
        except ValueError:
            continue
    return False


def money_in(texts: Iterable[str | None]) -> MoneyText:
    """What one message's texts (body, subject, document text layers) say about money."""
    plain = "\n".join(_plain(t) for t in texts if t)
    details = _has_iban(plain) or bool(_ACCOUNT.search(plain))
    demand = bool(_DEMAND.search(_ZERO_DUE.sub(" ", plain))) and (
        _has_amount(plain) or bool(_INVOICE_REF.search(plain))
    )
    return MoneyText(
        payment_details=details,
        payment_demand=demand,
        bank_change=details and bool(_CHANGE.search(plain)),
    )
