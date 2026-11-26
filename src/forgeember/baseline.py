"""Per-vendor and per-sender baselines, used by code for deterministic checks.

"Bank account changed" is decided here, by comparing normalized account identifiers
against the vendor's known accounts. Ember is never asked whether the bank changed. It's
asked only about manipulation in language and context.
"""

from __future__ import annotations

import json
import re
import unicodedata
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


# Letters from other scripts that look like Latin ones: the usual tools of lookalike domains
# and names ("fennim\u043ere" with a Cyrillic \u043e). Not Unicode's full confusables table (~6,000
# entries), only letters that are hard to tell apart in an inbox font.
_CONFUSABLES = str.maketrans(
    {
        # Cyrillic
        "\u0430": "a", "\u0435": "e", "\u0451": "e", "\u043e": "o", "\u0440": "p",
        "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0457": "i",
        "\u0458": "j", "\u0455": "s", "\u0501": "d", "\u051b": "q", "\u051d": "w",
        "\u04bb": "h", "\u04cf": "l",
        # Greek
        "\u03bf": "o", "\u03c1": "p", "\u03bd": "v", "\u03b9": "i", "\u03b1": "a",
        # Latin lookalikes
        "\u0131": "i", "\u0261": "g",
    }
)  # fmt: skip


def skeleton(text: str) -> str:
    """What a name or domain looks like to a reader: lowercase, accents dropped, letters from
    other scripts that look Latin mapped to the Latin letter."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return plain.translate(_CONFUSABLES)


def display_domain(domain: str) -> str:
    """A domain as a mail client may show it: punycode labels (xn--...) decoded."""
    try:
        return domain.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        return domain


def _norm_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    name = re.sub(r"\b(inc|llc|ltd|limited|co|corp|company|plc|gmbh)\b", " ", name)
    return " ".join(name.split())


def name_key(name: str) -> str:
    """A name as it sounds: normalized, spaces removed ("Marrow Vale" == "Marrowvale")."""
    return _norm_name(name).replace(" ", "")


# A spoken name is "heard" as one of our vendors within this many letters (whole name, spaces
# removed), and only when the name is long enough that 2 letters can't turn one into another.
SPOKEN_NAME_MAX_EDITS = 2
# A sender domain containing a vendor's whole domain name (this long or longer) imitates it.
MIN_EMBEDDED_LABEL = 6
SPOKEN_NAME_MIN_CHARS = 8


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
    # How the business already reaches this vendor. A handoff tells the owner to verify
    # through these, never through contact details given in the message being checked.
    phone: str | None = None
    contact_email: str | None = None


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
                    phone=v.get("phone"),
                    contact_email=v.get("contact_email"),
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

    def vendor_by_id(self, vendor_id: str | None) -> VendorProfile | None:
        return next((v for v in self.vendors if v.vendor_id == vendor_id), None)

    def vendor_by_name(self, name: str | None) -> VendorProfile | None:
        if not name:
            return None
        target = _norm_name(name)
        for v in self.vendors:
            if target in {_norm_name(n) for n in (v.name, *v.aliases)}:
                return v
        return None

    def vendor_by_spoken_name(self, name: str | None) -> VendorProfile | None:
        """A company name heard in a recording. Speech loses word spacing and confuses similar
        sounds ("Kestrel Bite Posting"), so after an exact match this accepts the single vendor
        within SPOKEN_NAME_MAX_EDITS letters. Used for voice only: written names stay exact.
        A match can only add a comparison with that vendor's records, never skip one."""
        exact = self.vendor_by_name(name)
        if exact is not None or not name:
            return exact
        key = name_key(name)
        if len(key) < SPOKEN_NAME_MIN_CHARS:
            return None
        hits = {
            v.vendor_id: v
            for v in self.vendors
            for n in (v.name, *v.aliases)
            if edit_distance(key, name_key(n)) <= SPOKEN_NAME_MAX_EDITS
        }
        if len(hits) == 1:
            return next(iter(hits.values()))
        # Live run (Nov 26): "Marrowvale Couriers" was heard as "Marrowvale Courier Services".
        # A vendor's distinctive first word (8+ letters) heard as a whole word, for exactly
        # one vendor, is enough.
        words = set(_norm_name(name).split())
        by_word = {
            v.vendor_id: v
            for v in self.vendors
            if (first := _norm_name(v.name).split()[0]) in words
            and len(first) >= SPOKEN_NAME_MIN_CHARS
        }
        return next(iter(by_word.values())) if len(by_word) == 1 and not hits else None

    def lookalike_vendor(self, domain: str | None) -> VendorProfile | None:
        """A sender domain that imitates a known vendor's, without being one on file:

        * within edit distance 2 of it ("fennimore-stationary.example"),
        * the same once look-alike letters are read as Latin, punycode decoded first
          ("xn--fennimre-...": "fennim\u043ere" with a Cyrillic \u043e), or
        * containing the vendor's whole domain name as a part ("fennimore-stationery-billing.
          example", "fennimore-stationery.example.other.example").
        """
        if not domain:
            return None
        domain = domain.lower().rstrip(".")
        if domain in self.known_domains or domain in self.organization.domains:
            return None
        shown = skeleton(display_domain(domain))
        for known, vendor in self.known_domains.items():
            if edit_distance(domain, known) <= 2 or edit_distance(shown, known) <= 2:
                return vendor
            label = known.rsplit(".", 1)[0]
            if len(label) >= MIN_EMBEDDED_LABEL and re.search(
                rf"(?:^|[.-]){re.escape(label)}(?:[.-]|$)", shown
            ):
                return vendor
        return None

    def lookalike_name_in(self, text: str | None) -> VendorProfile | None:
        """A vendor's name spelled with look-alike letters somewhere in a document's own text.
        Code reads the text layer itself, so this holds even when a model "helpfully" writes
        the name back in Latin letters."""
        if not text or text.isascii():
            return None
        for line in text.splitlines():
            if line.isascii():
                continue
            seen = " ".join(skeleton(line).split())
            raw = " ".join(line.lower().split())
            for v in self.vendors:
                for n in (v.name, *v.aliases):
                    if n.lower() in seen and n.lower() not in raw:
                        return v
        return None

    def lookalike_vendor_name(self, name: str | None) -> VendorProfile | None:
        """A vendor name written with look-alike letters from another script ("Fennim\u043ere
        Stationery" with a Cyrillic \u043e): not the vendor, imitating it."""
        if not name or name.isascii() or self.vendor_by_name(name) is not None:
            return None
        target = _norm_name(skeleton(name))
        for v in self.vendors:
            if target in {_norm_name(n) for n in (v.name, *v.aliases)}:
                return v
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
