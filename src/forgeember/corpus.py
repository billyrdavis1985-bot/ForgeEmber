"""Scale-run corpora: labeled email folders under data/ (gitignored).

* Benign mail is generated here, procedurally from a seed, for the fictional Ashgrove Bakery.
  We own it, so there are no license questions (docs/DATA.md).
* Phishing mail comes from the Nazario corpus (CC BY 4.0, verified in docs/DATA.md).
  ``split_mbox`` turns a downloaded mailbox into one ``.eml`` per message.

Every corpus folder has ``labels.jsonl``: one ``{"file", "label", "source"}`` line per item,
where label is "phish" or "benign".
"""

from __future__ import annotations

import json
import mailbox
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

BENIGN_VERSION = "benign-v1"
TO = "accounts@ashgrovebakery.example"

_FIRST = [
    "Priya",
    "Tom",
    "Aisha",
    "Marek",
    "Hannah",
    "Diego",
    "Mei",
    "Callum",
    "Sofia",
    "Kwame",
    "Ellen",
    "Ravi",
    "Grace",
    "Jonas",
    "Leila",
    "Owen",
    "Nadia",
    "Felix",
    "Rosa",
    "Ibrahim",
]
_LAST = [
    "Okafor",
    "Lindqvist",
    "Patel",
    "Moreau",
    "Kowalski",
    "Brennan",
    "Tanaka",
    "Silva",
    "Hughes",
    "Novak",
    "Achebe",
    "Fischer",
    "Quinn",
    "Rahman",
    "Dunmore",
    "Castillo",
]
_CUSTOMER_DOMAINS = ["mailbox.example", "postbox.example", "inboxly.example", "letterkit.example"]
_SUPPLIERS = [
    ("Fennimore Stationery", "fennimore-stationery.example"),
    ("Kestrel Byte Hosting", "kestrelbyte.example"),
    ("Marrowvale Couriers", "marrowvale-couriers.example"),
    ("Hollins Flour Mill", "hollinsflour.example"),
    ("Brookfield Dairy", "brookfielddairy.example"),
    ("Pennant Packaging", "pennantpack.example"),
]
_PRODUCTS = [
    "sourdough loaves",
    "croissants",
    "a birthday cake",
    "rye bread",
    "cinnamon buns",
    "a wedding cake tasting",
    "gluten-free brownies",
    "focaccia",
    "a tray of scones",
]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _person(rng: random.Random) -> tuple[str, str]:
    first, last = rng.choice(_FIRST), rng.choice(_LAST)
    return f"{first} {last}", f"{first.lower()}.{last.lower()}@{rng.choice(_CUSTOMER_DOMAINS)}"


def _t_customer_order(rng: random.Random) -> tuple[str, str, str, str]:
    name, addr = _person(rng)
    product, day = rng.choice(_PRODUCTS), rng.choice(_DAYS)
    qty = rng.randint(1, 30)
    return (
        name,
        addr,
        f"Order for {day}",
        f"Hello,\n\nCould I order {qty} {product} for collection on {day} morning? "
        f"Happy to pay in the shop when I collect.\n\nThanks,\n{name.split()[0]}\n",
    )


def _t_supplier_delivery(rng: random.Random) -> tuple[str, str, str, str]:
    supplier, domain = rng.choice(_SUPPLIERS)
    day = rng.choice(_DAYS)
    return (
        supplier,
        f"orders@{domain}",
        f"Delivery scheduled for {day}",
        f"Hello Ashgrove Bakery,\n\nYour order is scheduled for delivery on {day} between "
        f"{rng.randint(6, 10)}am and {rng.randint(1, 4)}pm. "
        "No action is needed.\n\nKind regards,\n"
        f"{supplier} Dispatch\n",
    )


def _t_payment_received(rng: random.Random) -> tuple[str, str, str, str]:
    supplier, domain = rng.choice(_SUPPLIERS)
    amount = f"{rng.randint(40, 900)}.{rng.randint(0, 99):02d}"
    return (
        supplier,
        f"accounts@{domain}",
        "Payment received, thank you",
        f"Hello,\n\nThis is to confirm we have received your payment of GBP {amount}. "
        "Your account is fully up to date.\n\nThank you,\n"
        f"{supplier} Accounts\n",
    )


def _t_meeting(rng: random.Random) -> tuple[str, str, str, str]:
    name, addr = _person(rng)
    day = rng.choice(_DAYS)
    return (
        name,
        addr,
        f"Catering chat on {day}?",
        f"Hi Dana,\n\nAre you free on {day} at {rng.randint(9, 16)}:00 to talk about catering "
        "for our office party next month? A short call is fine.\n\nBest,\n"
        f"{name}\n",
    )


def _t_review(rng: random.Random) -> tuple[str, str, str, str]:
    name, addr = _person(rng)
    product = rng.choice(_PRODUCTS)
    return (
        name,
        addr,
        "Thank you!",
        f"Just wanted to say the {product} were wonderful at our family lunch on "
        f"{rng.choice(_DAYS)}. We will be back soon!\n\n{name}\n",
    )


def _t_staff(rng: random.Random) -> tuple[str, str, str, str]:
    name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    addr = f"{name.split()[0].lower()}@ashgrovebakery.example"
    day = rng.choice(_DAYS)
    return (
        name,
        addr,
        f"Swap shifts on {day}?",
        f"Hi,\n\nWould it be OK if I swapped my {day} early shift with the late shift? "
        "I've checked and the rota still has two people on the counter.\n\nThanks,\n"
        f"{name.split()[0]}\n",
    )


def _t_hosting_notice(rng: random.Random) -> tuple[str, str, str, str]:
    return (
        "Kestrel Byte Support",
        "support@kestrelbyte.example",
        "Monthly website report",
        f"Hello,\n\nYour website had {rng.randint(800, 6000)} visits last month and "
        f"{rng.randint(99, 100)}.{rng.randint(0, 9)}% uptime. No issues were detected.\n\n"
        "Kestrel Byte Support\n",
    )


def _t_newsletter(rng: random.Random) -> tuple[str, str, str, str]:
    topic = rng.choice(
        [
            "rye flour prices",
            "oven maintenance",
            "seasonal menus",
            "food safety",
            "packaging regulations",
            "local markets",
        ]
    )
    return (
        "Flour Mill Weekly",
        "news@flourmill-weekly.example",
        f"This week: {topic}",
        f"Flour Mill Weekly\n\nIn this issue: {topic}, reader recipes, and upcoming trade "
        "events. You can unsubscribe at any time from our website.\n",
    )


def _t_price_list(rng: random.Random) -> tuple[str, str, str, str]:
    supplier, domain = rng.choice(_SUPPLIERS)
    pct = rng.randint(1, 6)
    return (
        supplier,
        f"sales@{domain}",
        "Updated price list for next quarter",
        f"Hello,\n\nPlease find our prices for next quarter below. Most items rise by {pct}% "
        "because of transport costs; nothing changes about how you order or pay.\n\n"
        f"{supplier} Sales\n",
    )


_TEMPLATES = [
    _t_customer_order,
    _t_supplier_delivery,
    _t_payment_received,
    _t_meeting,
    _t_review,
    _t_staff,
    _t_hosting_notice,
    _t_newsletter,
    _t_price_list,
]


def generate_benign(out_dir: Path, n: int, *, seed: str = BENIGN_VERSION) -> Path:
    """Write ``n`` fictional benign emails plus labels.jsonl. Deterministic for a given seed."""
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    labels = []
    for i in range(n):
        template = rng.choice(_TEMPLATES)
        name, addr, subject, body = template(rng)
        msg = EmailMessage()
        msg["From"] = f"{name} <{addr}>"
        msg["To"] = TO
        msg["Subject"] = subject
        msg["Date"] = format_datetime(start + timedelta(minutes=37 * i))
        msg["Message-ID"] = f"<{BENIGN_VERSION}-{i:06d}@corpus.forgeember.example>"
        msg.set_content(body)
        fname = f"benign-{i:06d}.eml"
        (out_dir / fname).write_bytes(msg.as_bytes())
        labels.append(
            {
                "file": fname,
                "label": "benign",
                "source": f"generated:{BENIGN_VERSION}",
                "template": template.__name__.removeprefix("_t_"),
            }
        )
    labels_path = out_dir / "labels.jsonl"
    labels_path.write_text("".join(json.dumps(x) + "\n" for x in labels), encoding="utf-8")
    return labels_path


@dataclass(frozen=True)
class SplitResult:
    written: int
    skipped_large: int
    skipped_broken: int


def split_mbox(
    mbox_path: Path,
    out_dir: Path,
    *,
    label: str,
    source: str,
    prefix: str,
    max_bytes: int = 1_000_000,
    limit: int | None = None,
) -> SplitResult:
    """One .eml per message, with labels.jsonl (appended). Skips oversized or broken messages."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = large = broken = 0
    lines = []
    box = mailbox.mbox(str(mbox_path), create=False)
    try:
        for idx, key in enumerate(box.iterkeys()):
            if limit is not None and written >= limit:
                break
            try:
                raw = box.get_bytes(key)
            except Exception:
                broken += 1
                continue
            if len(raw) > max_bytes:
                large += 1
                continue
            fname = f"{prefix}-{idx:06d}.eml"
            (out_dir / fname).write_bytes(raw)
            lines.append({"file": fname, "label": label, "source": source})
            written += 1
    finally:
        box.close()
    with (out_dir / "labels.jsonl").open("a", encoding="utf-8") as fh:
        fh.writelines(json.dumps(x) + "\n" for x in lines)
    return SplitResult(written, large, broken)


@dataclass(frozen=True)
class CorpusItem:
    path: Path
    label: str | None
    source: str | None


def load_corpus(paths: list[Path]) -> list[CorpusItem]:
    """Folders with labels.jsonl, or individual files (unlabeled)."""
    items: list[CorpusItem] = []
    for p in paths:
        if p.is_dir() and (p / "labels.jsonl").exists():
            for raw in (p / "labels.jsonl").read_text(encoding="utf-8").splitlines():
                if raw.strip():
                    d = json.loads(raw)
                    items.append(CorpusItem(p / d["file"], d.get("label"), d.get("source")))
        elif p.is_dir():
            items += [
                CorpusItem(f, None, None)
                for f in sorted(p.iterdir())
                if f.suffix.lower() in (".eml", ".pdf", ".png", ".jpg", ".jpeg", ".wav", ".mp3")
            ]
        else:
            items.append(CorpusItem(p, None, None))
    return items


def interleave(*groups: list[CorpusItem], seed: str = "mix") -> list[CorpusItem]:
    """Deterministic shuffle so a --limit run samples every source."""
    items = [x for g in groups for x in g]
    random.Random(seed).shuffle(items)
    return items


def iter_labels(path: Path) -> Iterator[dict]:
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            yield json.loads(raw)
