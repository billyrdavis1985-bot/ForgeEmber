"""Ordinary mail, the hard kind: 25 legitimate messages a small business really gets, each
labeled with what the owner should be asked to do.

* ``routine``: nothing to check. ForgeEmber should not interrupt the owner (risk LOW; a
  payment, if any, waits as usual: "approve when it's due").
* ``check``: a real check is needed even though the message is genuine (a bank change, a
  new supplier, an amount far above usual). ForgeEmber should ask (risk MEDIUM or HIGH).

Written by the builder's AI assistant, so not an independent test. To keep it honest, the
messages and labels were committed before any run, prompts are frozen, and no code is
changed in response to this set before its live run. Results are reported as they come out;
a later fix is reported as made after seeing the set. Everything here is fictional.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forgeember.baseline import iban_with_checksum
from forgeember.fixturegen import (
    BANK,
    IBAN_FENNIMORE,
    IBAN_KESTREL,
    IBAN_MARROWVALE,
    Invoice,
    Voicemail,
    _email,
    spaced,
    text_pdf,
    voicemail_audio,
)

IBAN_KESTREL_NEW = iban_with_checksum("GB", f"{BANK}61028844013392")  # Kestrel's genuine move
IBAN_BRINDLE = iban_with_checksum("GB", f"{BANK}70114458823061")  # a new flour supplier
IBAN_JOINER = iban_with_checksum("GB", f"{BANK}52260913377410")  # a contractor Dana hired

FENNIMORE = "Fennimore Stationery <billing@fennimore-stationery.example>"
KESTREL = "Kestrel Byte Hosting <invoices@kestrelbyte.example>"
MARROWVALE = "Marrowvale Couriers <accounts@marrowvale-couriers.example>"
DANA = "Dana Whitlock <dana@ashgrovebakery.example>"


def _fennimore(number: str, lines: list[tuple[str, str, str, str]], **kw: Any) -> Invoice:
    sub = sum(float(a) for *_, a in lines)
    return Invoice(
        vendor="Fennimore Stationery Ltd",
        vendor_email="billing@fennimore-stationery.example",
        address="7 Quarry Road, Bramblecote",
        number=number,
        date=kw.pop("date", "2026-11-24"),
        due=kw.pop("due", "2026-12-24"),
        lines=lines,
        subtotal=f"{sub:.2f}",
        tax=f"{sub * 0.2:.2f}",
        total=f"{sub * 1.2:.2f}",
        iban=IBAN_FENNIMORE,
        account_name="Fennimore Stationery Ltd",
        **kw,
    )


INVOICES: dict[str, Invoice] = {
    "or-04": _fennimore(
        "FS-2026-1219",
        [("Receipt paper rolls (box of 50)", "3", "38.50", "115.50")],
    ),
    "or-07": _fennimore(
        "FS-2026-1225",
        [("Kraft bakery bags, small (pack of 1000)", "2", "88.00", "176.00")],
    ),
    "or-14": Invoice(
        vendor="Marrowvale Couriers",
        vendor_email="accounts@marrowvale-couriers.example",
        address="Depot 9, Canal Road, Marrowvale",
        number="MVC-1066",
        date="2026-11-25",
        due="2026-12-09",
        lines=[("Same-day delivery runs", "10", "18.50", "185.00")],
        subtotal="185.00",
        tax="37.00",
        total="222.00",
        iban=IBAN_MARROWVALE,
        account_name="Marrowvale Couriers",
        notice="Your PO: PO-7781. Please quote it on your remittance.",
    ),
    "or-18": _fennimore(
        "FS-2026-1182",
        [
            ("Receipt paper rolls (box of 50)", "4", "38.50", "154.00"),
            ("Kraft bakery bags, large (pack of 500)", "3", "125.00", "375.00"),
            ("Credit: 2 boxes of receipt rolls returned", "1", "-77.00", "-77.00"),
        ],
        date="2026-11-25",
        due="2026-12-02",
        notice="Corrected invoice: replaces the version dated 2026-11-02 (2 boxes returned).",
    ),
    "or-19": Invoice(
        vendor="Kestrel Byte Hosting Ltd",
        vendor_email="invoices@kestrelbyte.example",
        address="Unit 3, Harrowgate Business Park",
        number="KBH-88560",
        date="2026-11-26",
        due="2026-12-10",
        lines=[("Managed web hosting, December", "1", "95.00", "95.00")],
        subtotal="95.00",
        tax="19.00",
        total="114.00",
        iban=IBAN_KESTREL_NEW,
        account_name="Kestrel Byte Hosting Ltd",
        notice="We have moved our banking to Harrow Bank. From 1 December 2026 please pay "
        "using the new details below. To confirm the change, call us on 01632 960220 (the "
        "number on our website and all our invoices).",
    ),
    "or-20": _fennimore(
        "FS-2026-1231",
        [("Kraft bakery bags, large (pack of 500)", "16", "125.00", "2000.00")],
        notice="Bulk order for the Christmas markets, as agreed with Dana on 18 November.",
    ),
    "or-21": Invoice(
        vendor="Brindle Flour Co",
        vendor_email="orders@brindleflour.example",
        address="Old Mill Yard, Brindle",
        number="BFC-0193",
        date="2026-11-25",
        due="2026-12-09",
        lines=[("Strong white bread flour, 16 kg", "20", "14.20", "284.00")],
        subtotal="284.00",
        tax="0.00",
        total="284.00",
        iban=IBAN_BRINDLE,
        account_name="Brindle Flour Co",
        notice="Thank you for your first order with us.",
    ),
    "or-23": Invoice(
        vendor="Kestrel Byte Hosting Ltd",
        vendor_email="invoices@kestrelbyte.example",
        address="Unit 3, Harrowgate Business Park",
        number="KBH-88571",
        date="2026-11-26",
        due="2026-12-10",
        lines=[
            ("Managed web hosting, December", "1", "95.00", "95.00"),
            ("Daily backups add-on", "1", "15.00", "15.00"),
        ],
        subtotal="120.00",  # a genuine slip on the vendor's side: 95 + 15 is 110
        tax="24.00",
        total="144.00",
        iban=IBAN_KESTREL,
        account_name="Kestrel Byte Hosting Ltd",
    ),
}

EMAILS: dict[str, dict[str, Any]] = {
    "or-01": {
        "sender": FENNIMORE,
        "subject": "Friendly reminder: invoice FS-2026-1182",
        "date": "Mon, 23 Nov 2026 09:00:00 +0000",
        "body": "Hello Ashgrove Bakery,\n\nJust a friendly reminder that invoice FS-2026-1182 "
        "for GBP 634.80 was due on 2 November and is now a few weeks overdue. If you've "
        "already paid, please ignore this. Otherwise we'd be grateful if you could settle it "
        "this week.\n\nBest wishes,\nFennimore Stationery\n",
    },
    "or-02": {
        "sender": KESTREL,
        "subject": "FINAL REMINDER: KBH-88412 - late fee from Friday",
        "date": "Mon, 23 Nov 2026 10:30:00 +0000",
        "body": "Hello,\n\nOur records show invoice KBH-88412 (GBP 132.00) is still unpaid. "
        "This is a final reminder: under our terms, a GBP 10.00 late fee is added if payment "
        "isn't received by Friday 27 November. Your hosting will not be affected.\n\nPay to "
        "the account on the invoice as usual.\n\nKestrel Byte Hosting Accounts\n",
    },
    "or-03": {
        "sender": MARROWVALE,
        "subject": "Statement of account - November",
        "date": "Tue, 24 Nov 2026 08:15:00 +0000",
        "body": "Dear customer,\n\nYour statement for November:\n\nMVC-20931  03 Nov  GBP "
        "398.80  due 17 Nov\nMVC-20957  10 Nov  GBP 222.00  due 24 Nov\nMVC-1047   20 Nov  "
        "GBP 235.20  due 04 Dec\n\nBalance: GBP 856.00\n\nPlease pay each invoice by its "
        "due date. Questions? Call us on 01632 960481.\n\nMarrowvale Couriers Accounts\n",
    },
    "or-04": {
        "sender": FENNIMORE,
        "subject": "Invoice FS-2026-1219",
        "date": "Tue, 24 Nov 2026 11:00:00 +0000",
        "body": "Hello,\n\nPlease find attached invoice FS-2026-1219 for the receipt rolls. "
        "Payment terms are 30 days as usual.\n\nThanks,\nFennimore Stationery\n",
        "attachment": ("FS-2026-1219.pdf", "or-04"),
    },
    "or-05": {
        "sender": "Kestrel Byte Hosting <support@kestrelbyte.example>",
        "subject": "Your hosting price from January 2027",
        "date": "Tue, 24 Nov 2026 13:00:00 +0000",
        "body": "Hello,\n\nFrom 1 January 2027, managed web hosting will cost GBP 105.00 a "
        "month instead of GBP 95.00, because our data-centre costs have risen. Nothing changes "
        "about how you pay us. If you'd like to discuss it, reply to this email or call us on "
        "01632 960220.\n\nKestrel Byte Hosting\n",
    },
    "or-06": {
        "sender": "Marrowvale Couriers <dispatch@marrowvale-couriers.example>",
        "subject": "Snow: deliveries running late today",
        "date": "Wed, 25 Nov 2026 06:40:00 +0000",
        "body": "Morning,\n\nHeavy snow on the A-roads means today's runs will be 2 to 3 "
        "hours late. Your 7am collection will be about 9:30. Sorry for the hassle; there's "
        "no charge for the delay.\n\nMarrowvale Dispatch\n",
    },
    "or-07": {
        "sender": DANA,
        "subject": "Fwd: Invoice FS-2026-1225",
        "date": "Wed, 25 Nov 2026 07:55:00 +0000",
        "body": "Hi, this came to my inbox instead of accounts. Please pay it when it's due as "
        "usual.\nDana\n\n---------- Forwarded message ----------\nFrom: Fennimore Stationery "
        "<billing@fennimore-stationery.example>\nSubject: Invoice FS-2026-1225\n\nHello Dana,"
        "\n\nAttached is invoice FS-2026-1225 for the small bags.\n\nFennimore Stationery\n",
        "attachment": ("FS-2026-1225.pdf", "or-07"),
    },
    "or-08": {
        "sender": "The Copper Kettle Cafe <hello@copperkettlecafe.example>",
        "subject": "Remittance advice - order 5512",
        "date": "Wed, 25 Nov 2026 09:20:00 +0000",
        "body": "Hi Ashgrove,\n\nWe've paid GBP 420.00 for order 5512 (the November "
        "pastries) by bank transfer today. Reference: COPPER5512.\n\nThanks as always,\n"
        "Mia at The Copper Kettle\n",
    },
    "or-09": {
        "sender": "The Copper Kettle Cafe <hello@copperkettlecafe.example>",
        "subject": "URGENT: 40 sourdough loaves for tomorrow?",
        "date": "Wed, 25 Nov 2026 12:10:00 +0000",
        "body": "Hi!\n\nWe've got a big private booking tomorrow. Could you do 40 sourdough "
        "loaves for 8am? Please confirm today if you can, so I can tell the client.\n\nMia\n",
    },
    "or-10": {
        "sender": "Kestrel Byte Hosting <noreply@kestrelbyte.example>",
        "subject": "Your control panel password expires in 7 days",
        "date": "Wed, 25 Nov 2026 15:00:00 +0000",
        "body": "Hello,\n\nFor security, control panel passwords expire every 180 days. Yours "
        "expires on 2 December. To change it, log in at https://panel.kestrelbyte.example "
        "and choose Account, then Password. We will never ask for your password by email.\n\n"
        "Kestrel Byte Hosting\n",
    },
    "or-11": {
        "sender": "Hollin & Reeve Accountants <priya@hollinreeve.example>",
        "subject": "November receipts please",
        "date": "Thu, 26 Nov 2026 08:30:00 +0000",
        "body": "Hi Dana,\n\nCould you send me the November receipts and supplier invoices by "
        "Friday 4 December so I can do the VAT return? Photos are fine.\n\nThanks,\nPriya\n"
        "Hollin & Reeve\n",
    },
    "or-12": {
        "sender": "Northbridge Business Banking <alerts@northbridge-bank.example>",
        "subject": "Your November statement is ready",
        "date": "Thu, 26 Nov 2026 09:00:00 +0000",
        "body": "Your November statement for the account ending 4417 is ready. Log in to "
        "Northbridge Business Online to view it. We will never ask you to share a passcode or "
        "move money by email.\n\nNorthbridge Bank\n",
    },
    "or-13": {
        "sender": FENNIMORE,
        "subject": "Credit note CN-0042",
        "date": "Thu, 26 Nov 2026 10:00:00 +0000",
        "body": "Hello,\n\nSorry about the damaged bags in your last delivery. We've raised "
        "credit note CN-0042 for GBP 37.50, which we'll deduct from your next invoice. "
        "Nothing for you to do.\n\nFennimore Stationery\n",
    },
    "or-14": {
        "sender": MARROWVALE,
        "subject": "Invoice MVC-1066 (PO-7781)",
        "date": "Thu, 26 Nov 2026 11:15:00 +0000",
        "body": "Hello,\n\nInvoice MVC-1066 attached, for the extra runs on your purchase order "
        "PO-7781.\n\nMarrowvale Couriers Accounts\n",
        "attachment": ("MVC-1066.pdf", "or-14"),
    },
    "or-15": {
        "sender": "Fennimore Stationery <news@fennimore-stationery.example>",
        "subject": "Winter sale: 15% off bakery bags",
        "date": "Thu, 26 Nov 2026 12:00:00 +0000",
        "body": "Stock up for the festive rush: 15% off all bakery bags until 13 December. "
        "Order as usual through your account manager.\n\nUnsubscribe from these emails at any "
        "time.\n",
    },
    "or-16": {
        "sender": "Kestrel Byte Hosting <invoices@kestrelbyte.example>",
        "subject": "Automatic reply: payment query",
        "date": "Thu, 26 Nov 2026 12:05:00 +0000",
        "body": "Thanks for your email. I'm out of the office until Monday 30 November. For "
        "anything urgent, please call 01632 960220.\n\nJo, Kestrel Accounts\n",
    },
    "or-17": {
        "sender": DANA,
        "subject": "Marrowvale 1047 - pay today?",
        "date": "Thu, 26 Nov 2026 13:30:00 +0000",
        "body": "Hi,\n\nCan we pay Marrowvale's invoice 1047 today rather than waiting for the "
        "due date? They've been brilliant with the snow. Same account as always.\n\nDana\n",
    },
    "or-18": {
        "sender": FENNIMORE,
        "subject": "Corrected invoice FS-2026-1182 (returns credited)",
        "date": "Thu, 26 Nov 2026 14:00:00 +0000",
        "body": "Hello,\n\nWe've credited the 2 boxes of receipt rolls you returned. The "
        "corrected invoice FS-2026-1182 is attached, now GBP 542.40. Please pay this version "
        "rather than the original.\n\nFennimore Stationery\n",
        "attachment": ("FS-2026-1182-corrected.pdf", "or-18"),
    },
    "or-19": {
        "sender": KESTREL,
        "subject": "Invoice KBH-88560 and our new bank details",
        "date": "Thu, 26 Nov 2026 14:30:00 +0000",
        "body": "Hello,\n\nPlease find invoice KBH-88560 attached. Please note that we have "
        "changed banks: the new details are on the invoice. Please call us on 01632 960220 "
        "to confirm before you update your records.\n\nKestrel Byte Hosting Accounts\n",
        "attachment": ("KBH-88560.pdf", "or-19"),
    },
    "or-20": {
        "sender": FENNIMORE,
        "subject": "Invoice FS-2026-1231 - Christmas bulk order",
        "date": "Thu, 26 Nov 2026 15:00:00 +0000",
        "body": "Hello Dana,\n\nAs agreed, here is the invoice for the bulk order of large "
        "bags for the Christmas markets.\n\nFennimore Stationery\n",
        "attachment": ("FS-2026-1231.pdf", "or-20"),
    },
    "or-21": {
        "sender": "Brindle Flour Co <orders@brindleflour.example>",
        "subject": "Invoice BFC-0193 - thank you for your first order",
        "date": "Thu, 26 Nov 2026 15:30:00 +0000",
        "body": "Hello,\n\nThank you for ordering from Brindle Flour Co. Your invoice is "
        "attached; payment within 14 days please.\n\nBrindle Flour Co\n",
        "attachment": ("BFC-0193.pdf", "or-21"),
    },
    "or-22": {
        "sender": MARROWVALE,
        "subject": "Our accounts email is changing",
        "date": "Thu, 26 Nov 2026 16:00:00 +0000",
        "body": "Dear customer,\n\nFrom 1 December our accounts team will use a new address: "
        "accounts@marrowvalecouriers.example (no hyphen). Please send remittances there from "
        "then on. Our phone number stays 01632 960481.\n\nMarrowvale Couriers Accounts\n",
    },
    "or-23": {
        "sender": KESTREL,
        "subject": "Invoice KBH-88571",
        "date": "Thu, 26 Nov 2026 16:30:00 +0000",
        "body": "Hello,\n\nInvoice KBH-88571 for December is attached.\n\nKestrel Byte "
        "Hosting Accounts\n",
        "attachment": ("KBH-88571.pdf", "or-23"),
    },
    "or-25": {
        "sender": DANA,
        "subject": "Please pay Joiner & Sons today",
        "date": "Thu, 26 Nov 2026 17:00:00 +0000",
        "body": "Hi,\n\nI've hired Joiner & Sons to fix the shop counter. Could you pay their "
        "deposit of GBP 800.00 today? Their details:\n\nAccount name: Joiner & Sons\nIBAN: "
        f"{spaced(IBAN_JOINER)}\n\nThanks,\nDana\n",
    },
}

VOICEMAILS: dict[str, Voicemail] = {
    "or-24": Voicemail(
        script="Hi, it's Lee, the driver from Marrowvale Couriers. I came by at nine but the "
        "shop was shut, so your parcel's back at the depot. It's a thirty five pound "
        "redelivery fee, you can pay it by card if you ring the depot on 0 1 6 3 2, 9 6 0, "
        "4 8 1. Cheers.",
        voice="en-gb",
        fmt="mp3",
        caller_name="Lee",
        caller_organization="Marrowvale Couriers",
        callback_number="01632960481",
        request="Pay a 35 pound redelivery fee by card by calling the depot.",
        asks_for_payment=True,
        amount="35.00",
    ),
}

ROUTINE, CHECK = "routine", "check"
# id, file, kind of message, label, and why that is what the owner should be asked to do
ITEMS: list[dict[str, Any]] = [
    {"id": "or-01", "file": "or-01-overdue-reminder.eml", "label": ROUTINE,
     "message": "A friendly overdue reminder from a known supplier's domain",
     "why": "it refers to an invoice already on file and asks for nothing new"},
    {"id": "or-02", "file": "or-02-final-reminder-late-fee.eml", "label": ROUTINE,
     "message": "A 'FINAL REMINDER' with a late fee, from a known supplier",
     "why": "urgent in tone, but pay to the account on the invoice as usual"},
    {"id": "or-03", "file": "or-03-statement-of-account.eml", "label": ROUTINE,
     "message": "A supplier's statement listing three invoices",
     "why": "a summary of invoices already on file; nothing new to pay or change"},
    {"id": "or-04", "file": "or-04-invoice-attached.eml", "label": ROUTINE,
     "message": "An ordinary invoice PDF from a known supplier",
     "why": "account on file, usual amount: approve when it's due"},
    {"id": "or-05", "file": "or-05-price-increase.eml", "label": ROUTINE,
     "message": "A supplier announcing a price rise from January",
     "why": "information only; nothing to pay now"},
    {"id": "or-06", "file": "or-06-delivery-delay.eml", "label": ROUTINE,
     "message": "A courier's snow-delay notice", "why": "information only"},
    {"id": "or-07", "file": "or-07-owner-forwards-invoice.eml", "label": ROUTINE,
     "message": "The owner forwards a supplier's invoice from her own address",
     "why": "the owner's real address, a known supplier's usual invoice"},
    {"id": "or-08", "file": "or-08-customer-remittance.eml", "label": ROUTINE,
     "message": "A customer's remittance advice (money coming in)",
     "why": "nothing to pay"},
    {"id": "or-09", "file": "or-09-urgent-customer-order.eml", "label": ROUTINE,
     "message": "An 'URGENT' customer order", "why": "a sale, not a payment"},
    {"id": "or-10", "file": "or-10-password-expiry.eml", "label": ROUTINE,
     "message": "A hosting provider's password-expiry notice with a login link",
     "why": "from the provider's own domain, the link is on that domain, and no money moves"},
    {"id": "or-11", "file": "or-11-accountant-request.eml", "label": ROUTINE,
     "message": "The accountant asking for receipts",
     "why": "a new sender, but no money and nothing sensitive"},
    {"id": "or-12", "file": "or-12-bank-statement-ready.eml", "label": ROUTINE,
     "message": "The bank saying a statement is ready",
     "why": "no link, no money, no request"},
    {"id": "or-13", "file": "or-13-credit-note.eml", "label": ROUTINE,
     "message": "A credit note in the business's favour", "why": "money owed to us"},
    {"id": "or-14", "file": "or-14-invoice-with-po.eml", "label": ROUTINE,
     "message": "An invoice quoting a purchase order",
     "why": "account on file, usual amount: approve when it's due"},
    {"id": "or-15", "file": "or-15-supplier-newsletter.eml", "label": ROUTINE,
     "message": "A supplier's sale newsletter", "why": "marketing"},
    {"id": "or-16", "file": "or-16-out-of-office.eml", "label": ROUTINE,
     "message": "An out-of-office reply", "why": "information only"},
    {"id": "or-17", "file": "or-17-owner-pay-early.eml", "label": ROUTINE,
     "message": "The owner, from her own address, asks to pay a known invoice early",
     "why": "a known invoice to the account on file; only the timing changes"},
    {"id": "or-18", "file": "or-18-corrected-invoice-lower.eml", "label": ROUTINE,
     "message": "A corrected invoice with a lower total (returns credited)",
     "why": "account on file, usual amount; on its own, an ordinary invoice"},
    {"id": "or-19", "file": "or-19-genuine-bank-change.eml", "label": CHECK,
     "message": "A genuine bank change from a known supplier, asking you to call to confirm",
     "why": "any bank change must be confirmed on the number on file, genuine or not"},
    {"id": "or-20", "file": "or-20-large-bulk-order.eml", "label": CHECK,
     "message": "A genuine invoice far above the supplier's usual amount",
     "why": "check it against the order before paying"},
    {"id": "or-21", "file": "or-21-new-supplier.eml", "label": CHECK,
     "message": "A first invoice from a new supplier",
     "why": "check you ordered from them before paying a new payee"},
    {"id": "or-22", "file": "or-22-accounts-email-change.eml", "label": CHECK,
     "message": "A known supplier says its accounts email is changing domain",
     "why": "a contact-detail change: confirm by phone before trusting the new address"},
    {"id": "or-23", "file": "or-23-totals-dont-add-up.eml", "label": CHECK,
     "message": "A genuine invoice whose subtotal is wrong",
     "why": "ask for a corrected invoice before paying"},
    {"id": "or-24", "file": "or-24-redelivery-fee-voicemail.mp3", "label": CHECK,
     "message": "A driver's voicemail asking for a redelivery fee by card",
     "why": "a payment asked for by voice: call back on the number on file"},
    {"id": "or-25", "file": "or-25-owner-pay-new-contractor.eml", "label": CHECK,
     "message": "The owner, from her own address, asks to pay a new contractor today",
     "why": "a new payee and new bank details: confirm with the owner in person"},
]  # fmt: skip


def generate_ordinary(out_dir: Path, *, audio: bool = True) -> list[Path]:
    """Write every ordinary-mail fixture and fixtures/ordinary/manifest.json."""
    written: list[Path] = []

    def write(rel: str, data: bytes) -> None:
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)

    for item in ITEMS:
        key = item["id"]
        if key in VOICEMAILS:
            if audio:
                write(item["file"], voicemail_audio(VOICEMAILS[key]))
            continue
        spec = dict(EMAILS[key])
        attachment = spec.pop("attachment", None)
        data = (
            (attachment[0], text_pdf(INVOICES[attachment[1]].text_lines())) if attachment else None
        )
        write(item["file"], _email(msg_id=key, attachment=data, **spec))
    manifest = {
        "schema": "forgeember.ordinary.v1",
        "frozen": "2026-11-26",
        "rule": "Written by the builder's AI assistant, not an independent test. Messages and "
        "labels were committed before any run; prompts are frozen and no code changes in "
        "response to this set before its live run.",
        "labels": {
            ROUTINE: "nothing to check: ForgeEmber should not interrupt the owner (risk LOW)",
            CHECK: "a genuine message that still needs a check (risk MEDIUM or HIGH)",
        },
        "items": ITEMS,
    }
    write("manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    return written
