"""Per-vendor and per-sender baselines, used by code for deterministic checks.

"Bank account changed" is decided here, by comparing normalized account identifiers
against the vendor's known accounts. Ember is never asked whether the bank changed. It's
asked only about manipulation in language and context.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any


class BankStatus(StrEnum):
    MATCH = "match"  # a known account for this vendor
    CHANGED = "changed"  # vendor known, account not one of theirs
    MISSING = "missing"  # no bank details on the item
    NO_BASELINE = "no_baseline"  # vendor unknown


class AmountStatus(StrEnum):
    USUAL = "usual"
    UNUSUAL = "unusual"
    NO_BASELINE = "no_baseline"
    UNPARSEABLE = "unparseable"


def normalize_iban(value: str) -> str:
    return re.sub(r"[\s-]+", "", value).upper()


def iban_is_valid(value: str) -> bool:
    """ISO 13616 mod-97 check. Catches typos and many hand-made fake IBANs."""
    iban = normalize_iban(value)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def iban_with_checksum(country: str, bban: str) -> str:
    """Build a valid IBAN from a country code and BBAN (used by the fixture generator)."""
    rearranged = bban + country + "00"
    digits = "".join(str(int(ch, 36)) for ch in rearranged.upper())
    check = 98 - int(digits) % 97
    return f"{country}{check:02d}{bban}".upper()


def account_key(
    *, iban: str | None = None, account_number: str | None = None, routing_number: str | None = None
) -> str | None:
    """A normalized identity for a bank account, or None if nothing usable is present."""
    if iban:
        return f"IBAN:{normalize_iban(iban)}"
    if account_number:
        acct = re.sub(r"\D", "", account_number)
        routing = re.sub(r"\D", "", routing_number or "")
        return f"ACCT:{routing}/{acct}" if acct else None
    return None


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _norm_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    name = re.sub(r"\b(inc|llc|ltd|limited|co|corp|company|plc|gmbh)\b", " ", name)
    return " ".join(name.split())


def parse_amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value.replace(",", ""))
    try:
        return Decimal(cleaned) if cleaned not in ("", "-", ".") else None
    except InvalidOperation:
        return None


@dataclass(frozen=True, slots=True)
class VendorProfile:
    vendor_id: str
    name: str
    aliases: tuple[str, ...]
    email_domains: tuple[str, ...]
    bank_accounts: tuple[str, ...]  # account_key() values
    currency: str
    typical_total_min: Decimal
    typical_total_max: Decimal


@dataclass(frozen=True, slots=True)
class Executive:
    name: str
    email: str
    title: str


@dataclass(frozen=True, slots=True)
class Organization:
    name: str
    domains: tuple[str, ...]
    executives: tuple[Executive, ...]


@dataclass(frozen=True, slots=True)
class BaselineStore:
    organization: Organization
    vendors: tuple[VendorProfile, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineStore:
        org = data["organization"]
        return cls(
            organization=Organization(
                name=org["name"],
                domains=tuple(d.lower() for d in org["domains"]),
                executives=tuple(Executive(**e) for e in org.get("executives", [])),
            ),
            vendors=tuple(
                VendorProfile(
                    vendor_id=v["vendor_id"],
                    name=v["name"],
                    aliases=tuple(v.get("aliases", [])),
                    email_domains=tuple(d.lower() for d in v["email_domains"]),
                    bank_accounts=tuple(
                        k for k in (account_key(**b) for b in v["bank_accounts"]) if k
                    ),
                    currency=v["currency"],
                    typical_total_min=Decimal(v["typical_total_min"]),
                    typical_total_max=Decimal(v["typical_total_max"]),
                )
                for v in data["vendors"]
            ),
        )

    @classmethod
    def load(cls, path: str | Path) -> BaselineStore:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def known_domains(self) -> dict[str, VendorProfile]:
        return {d: v for v in self.vendors for d in v.email_domains}

    def vendor_by_domain(self, domain: str | None) -> VendorProfile | None:
        return self.known_domains.get(domain.lower()) if domain else None

    def vendor_by_name(self, name: str | None) -> VendorProfile | None:
        if not name:
            return None
        target = _norm_name(name)
        for v in self.vendors:
            if target in {_norm_name(n) for n in (v.name, *v.aliases)}:
                return v
        return None

    def lookalike_vendor(self, domain: str | None) -> VendorProfile | None:
        """A sender domain within edit distance 2 of a known vendor domain, but not equal."""
        if not domain:
            return None
        domain = domain.lower()
        if domain in self.known_domains or domain in self.organization.domains:
            return None
        for known, vendor in self.known_domains.items():
            if edit_distance(domain, known) <= 2:
                return vendor
        return None

    def is_internal(self, domain: str | None) -> bool:
        return bool(domain) and domain.lower() in self.organization.domains

    def impersonated_executive(self, from_name: str, from_addr: str) -> Executive | None:
        """Display name matches one of our executives, but the address isn't theirs."""
        name = _norm_name(from_name)
        if not name:
            return None
        for ex in self.organization.executives:
            if _norm_name(ex.name) == name and from_addr.lower() != ex.email.lower():
                return ex
        return None

    def executive_named(self, name: str | None) -> Executive | None:
        """An executive whose full or first name the caller gives (a voice proves nothing)."""
        target = _norm_name(name or "")
        if not target:
            return None
        for ex in self.organization.executives:
            full = _norm_name(ex.name)
            if target in (full, full.split(" ")[0]):
                return ex
        return None

    def bank_status(self, vendor: VendorProfile | None, key: str | None) -> BankStatus:
        if key is None:
            return BankStatus.MISSING
        if vendor is None:
            return BankStatus.NO_BASELINE
        return BankStatus.MATCH if key in vendor.bank_accounts else BankStatus.CHANGED

    def amount_status(self, vendor: VendorProfile | None, total: str | None) -> AmountStatus:
        amount = parse_amount(total)
        if amount is None:
            return AmountStatus.UNPARSEABLE
        if vendor is None:
            return AmountStatus.NO_BASELINE
        if vendor.typical_total_min <= amount <= vendor.typical_total_max:
            return AmountStatus.USUAL
        return AmountStatus.UNUSUAL
