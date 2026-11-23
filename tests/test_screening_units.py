"""Unit tests for the code-side authority layers: envelope, baselines, schemas, verify, policy."""

import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from forgeember import envelope
from forgeember.baseline import (
    AmountStatus,
    BankStatus,
    BaselineStore,
    account_key,
    edit_distance,
    iban_is_valid,
    iban_with_checksum,
    parse_amount,
)
from forgeember.budget.ledger import InputKind
from forgeember.fixturegen import text_pdf
from forgeember.policy import Facts, Risk, Tier, action, decide
from forgeember.schemas import (
    InvoiceExtraction,
    RiskAssessment,
    SkepticView,
    TriageResult,
    strict_json_schema,
    text_format,
)
from forgeember.verify import CheckStatus, quote_status, verify_invoice

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")


# ---------------------------------------------------------------- envelope
def _eml(**kw) -> bytes:
    msg = EmailMessage()
    msg["From"] = kw.get("sender", "A <a@x.example>")
    msg["To"] = "accounts@ashgrovebakery.example"
    msg["Subject"] = "s"
    if "html" in kw:
        msg.set_content("plain fallback")
        msg.add_alternative(kw["html"], subtype="html")
    else:
        msg.set_content(kw.get("body", "hello"))
    if "attach" in kw:
        data, maintype, subtype, name = kw["attach"]
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()


def test_sniffing_uses_bytes_not_names():
    assert envelope.sniff_mime(b"%PDF-1.4 ...") == "application/pdf"
    assert envelope.sniff_mime(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert envelope.sniff_mime(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert envelope.sniff_mime(b"\x00\x00\x00\x18ftypmp42") == "video/mp4"
    assert envelope.sniff_mime(b"MZ\x90\x00\x03") == "application/octet-stream"


def test_pdf_envelope_has_verifiable_text_and_stable_id():
    data = text_pdf(["Invoice number: X-1", "Total due: GBP 10.00"])
    a = envelope.from_bytes(data, source="a.pdf", filename="a.pdf")
    b = envelope.from_bytes(data, source="other/name.pdf", filename="name.pdf")
    assert a.id == b.id and a.id.startswith("itm_")
    assert a.kind is InputKind.PDF
    assert "Total due: GBP 10.00" in a.parts[0].text


def test_email_with_disguised_attachment_flags_mime_mismatch():
    raw = _eml(attach=(b"MZ\x90\x00 not a pdf", "application", "pdf", "invoice.pdf"))
    env = envelope.from_bytes(raw, source="m.eml")
    att = env.part("attachment:invoice.pdf")
    assert att.mime == "application/octet-stream" and att.mime_mismatch
    assert env.headers.from_domain == "x.example"


def test_html_body_becomes_text():
    env = envelope.from_bytes(
        _eml(html="<p>Pay <b>now</b></p><script>x()</script>"), source="h.eml"
    )
    assert env.part("body").text.strip() in ("plain fallback", "Pay now")


def test_unsupported_content_rejected():
    with pytest.raises(ValueError):
        envelope.from_bytes(b"\x00\x01\x02\xff\xfe", source="blob")


def test_headers_are_a_citable_part():
    env = envelope.from_bytes(
        _eml(sender="Dana Whitlock <dana@evil.example>", body="hello"), source="m.eml"
    )
    headers = env.part("headers")
    assert headers is not None and headers.text is not None
    assert quote_status(env, "headers", "Dana Whitlock <dana@evil.example>") is CheckStatus.PASS
    assert quote_status(env, "headers", "not in the headers") is CheckStatus.FAIL


# ---------------------------------------------------------------- baselines
def test_iban_checksum_and_builder():
    good = iban_with_checksum("GB", "ZZTB40051641820073")
    assert iban_is_valid(good) and iban_is_valid(" ".join(good[i : i + 4] for i in range(0, 22, 4)))
    assert not iban_is_valid(good[:2] + "00" + good[4:])
    assert not iban_is_valid("not an iban")
    assert iban_is_valid("GB82 WEST 1234 5698 7654 32")  # the standard published example


def test_bank_and_amount_status():
    v = BASELINES.vendor_by_domain("fennimore-stationery.example")
    known = v.bank_accounts[0]
    assert BASELINES.bank_status(v, known) is BankStatus.MATCH
    assert (
        BASELINES.bank_status(v, account_key(iban="GB82WEST12345698765432")) is BankStatus.CHANGED
    )
    assert BASELINES.bank_status(v, None) is BankStatus.MISSING
    assert BASELINES.bank_status(None, known) is BankStatus.NO_BASELINE
    assert BASELINES.amount_status(v, "634.80") is AmountStatus.USUAL
    assert BASELINES.amount_status(v, "9,000.00") is AmountStatus.UNUSUAL
    assert BASELINES.amount_status(v, "n/a") is AmountStatus.UNPARSEABLE


def test_vendor_lookup_lookalike_and_impersonation():
    assert BASELINES.vendor_by_name("FENNIMORE STATIONERY, LTD.").vendor_id == "fennimore"
    assert BASELINES.lookalike_vendor("fennimore-stationary.example").vendor_id == "fennimore"
    assert BASELINES.lookalike_vendor("fennimore-stationery.example") is None
    assert BASELINES.lookalike_vendor("totally-different.example") is None
    assert (
        BASELINES.impersonated_executive("Dana Whitlock", "dana@elsewhere.example").name
        == "Dana Whitlock"
    )
    assert BASELINES.impersonated_executive("Dana Whitlock", "dana@ashgrovebakery.example") is None
    assert edit_distance("kitten", "sitting") == 3
    assert parse_amount("GBP 1,234.50") == parse_amount("1234.50")
    assert (
        account_key(account_number="12-345 678", routing_number="021000021")
        == "ACCT:021000021/12345678"
    )


# ---------------------------------------------------------------- schemas
@pytest.mark.parametrize("model", [TriageResult, InvoiceExtraction, RiskAssessment, SkepticView])
def test_schemas_fit_metas_strict_subset(model):
    schema = strict_json_schema(model)
    assert schema["type"] == "object"

    def walk(node):
        if isinstance(node, dict):
            assert "allOf" not in node and "oneOf" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    fmt = text_format(model, "x")
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    json.dumps(fmt)  # serializable


def test_models_reject_extra_fields():
    with pytest.raises(ValueError):
        RiskAssessment.model_validate(
            {"verdict": "benign", "signals": [], "rationale": "", "extra": 1}
        )


# ---------------------------------------------------------------- verify
def _invoice(**over):
    base = {
        "is_invoice": True,
        "vendor_name": "V",
        "vendor_email": None,
        "invoice_number": "X-1",
        "invoice_date": None,
        "due_date": None,
        "currency": "GBP",
        "line_items": [
            {"description": "a", "quantity": "2", "unit_price": "5.00", "amount": "10.00"}
        ],
        "subtotal": "10.00",
        "tax": "2.00",
        "total": "12.00",
        "bank": {
            "account_name": None,
            "iban": "GB82 WEST 1234 5698 7654 32",
            "account_number": None,
            "routing_number": None,
            "swift_bic": None,
        },
        "claims_bank_change": False,
        "evidence": [{"field": "total", "part": "f.pdf", "quote": "Total due:   GBP 12.00"}],
    }
    base.update(over)
    return InvoiceExtraction.model_validate(base)


def _pdf_env(lines):
    return envelope.from_bytes(text_pdf(lines), source="f.pdf", filename="f.pdf")


def test_verify_invoice_passes_on_consistent_document():
    env = _pdf_env(
        ["Invoice number: X-1", "Total due: GBP 12.00", "IBAN: GB82 WEST 1234 5698 7654 32"]
    )
    checks = {c.name: c.status for c in verify_invoice(env, _invoice())}
    assert set(checks.values()) == {CheckStatus.PASS}, checks


def test_verify_invoice_catches_math_and_missing_values():
    env = _pdf_env(["Invoice number: X-1", "Total due: GBP 12.00"])
    bad = _invoice(
        total="13.00",
        line_items=[{"description": "a", "quantity": "2", "unit_price": "5.00", "amount": "11.00"}],
    )
    checks = {c.name: c.status for c in verify_invoice(env, bad)}
    assert checks["invoice.math.line[0]"] is CheckStatus.FAIL
    assert checks["invoice.math.total"] is CheckStatus.FAIL
    assert checks["invoice.in_source:total"] is CheckStatus.FAIL
    assert checks["invoice.in_source:iban"] is CheckStatus.FAIL  # IBAN never appears in the text


def test_quotes_from_images_are_unverifiable_not_passed():
    png = (FIXTURES / "invoices" / "inv-003-kestrel-scan.png").read_bytes()
    env = envelope.from_bytes(png, source="scan.png", filename="scan.png")
    assert quote_status(env, "scan.png", "Total due") is CheckStatus.UNVERIFIABLE
    assert quote_status(env, "no-such-part", "x") is CheckStatus.FAIL


# ---------------------------------------------------------------- policy
def test_t2_can_never_be_executed():
    with pytest.raises(ValueError):
        type(action("pay", "x"))(kind="pay", tier=Tier.T2, disposition="execute", reason="x")
    with pytest.raises(ValueError):
        type(action("file", "x"))(kind="pay", tier=Tier.T0, disposition="execute", reason="x")
    assert action("mystery_action", "x").disposition == "hold"


def test_unverified_model_fraud_claim_is_only_medium():
    d = decide(
        Facts(
            category="payment_request",
            vendor_known=True,
            ember_verdict="likely_fraud",
            verified_signals=0,
        )
    )
    assert d.risk is Risk.MEDIUM
    d = decide(
        Facts(
            category="payment_request",
            vendor_known=True,
            ember_verdict="likely_fraud",
            verified_signals=2,
        )
    )
    assert d.risk is Risk.HIGH


def test_model_cannot_lower_code_risk():
    from forgeember.baseline import BankStatus as B

    d = decide(
        Facts(category="invoice", vendor_known=True, bank_status=B.CHANGED, ember_verdict="benign")
    )
    assert d.risk is Risk.HIGH
    assert {a.kind for a in d.held} == {"pay"}


def test_missing_bank_details_on_a_money_item_is_medium():
    d = decide(Facts(category="invoice", vendor_known=True, bank_status=BankStatus.MISSING))
    assert d.risk is Risk.MEDIUM
    assert any("bank details" in r for r in d.reasons)
    assert {a.kind for a in d.held} == {"pay"}


def test_normal_mail_is_only_filed_and_tagged():
    d = decide(Facts(category="normal_business"))
    assert d.risk is Risk.LOW
    assert [a.kind for a in d.actions] == ["file", "tag", "route_note"]
    assert d.held == ()
