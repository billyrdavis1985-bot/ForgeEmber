"""Scale run: corpora, bulk mode, and the resumable budget-capped batch runner."""

import json
import mailbox
from email.message import EmailMessage
from pathlib import Path

from conftest import responses_payload
from forgeember import envelope
from forgeember.baseline import BaselineStore
from forgeember.batch.runner import run_batch
from forgeember.budget.ledger import BudgetConfig
from forgeember.budget.pricing import usd_to_nusd
from forgeember.corpus import generate_benign, interleave, load_corpus, split_mbox
from forgeember.ember.client import EmberClient, TransportResponse
from forgeember.pipeline import Screener, SkepticMode, code_findings, injection_attempt

ROOT = Path(__file__).resolve().parents[1]
BASELINES = BaselineStore.load(ROOT / "fixtures" / "baselines.json")
PHISH_PHRASE = "verify your account within 24 hours"


class ContentEmber:
    """Triage-only fake: flags mail containing the phishing phrase, with a real quote."""

    def __init__(self):
        self.calls = 0

    def post_json(self, path, body):
        self.calls += 1
        text = json.dumps(body["input"])
        phish = PHISH_PHRASE in text
        name = body["text"]["format"]["name"]
        assert name == "triage_result", f"bulk mode should only triage plain mail, got {name}"
        answer = {
            "category": "other" if phish else "normal_business",
            "summary": "s",
            "needs_document_extraction": False,
            "risk_signals": [
                {
                    "type": "urgency",
                    "part": "body",
                    "quote": PHISH_PHRASE,
                    "explanation": "deadline pressure",
                }
            ]
            if phish
            else [],
            "escalate": phish,
        }
        return TransportResponse(
            200,
            responses_payload(
                text=json.dumps(answer),
                input_tokens=900,
                cached=700,
                output_tokens=150,
                reasoning=60,
            ),
        )


def _phish_corpus(dir_: Path, n: int) -> Path:
    dir_.mkdir(parents=True)
    lines = []
    for i in range(n):
        msg = EmailMessage()
        msg["From"] = "Security Team <alerts@account-check.example>"
        msg["To"] = "accounts@ashgrovebakery.example"
        msg["Subject"] = f"Action required {i}"
        msg.set_content(f"Dear customer, please {PHISH_PHRASE} or access is suspended. Ref {i}")
        (dir_ / f"p{i}.eml").write_bytes(msg.as_bytes())
        lines.append(json.dumps({"file": f"p{i}.eml", "label": "phish", "source": "test"}))
    (dir_ / "labels.jsonl").write_text("\n".join(lines) + "\n")
    return dir_


def _screen(ledger, ember, **kw):
    client = EmberClient(ledger=ledger, stage="scale", transport=ember)
    return Screener(client=client, baselines=BASELINES, bulk=True, **kw).screen


def test_benign_corpus_is_deterministic_and_clean(tmp_path):
    a = generate_benign(tmp_path / "a", 60)
    b = generate_benign(tmp_path / "b", 60)
    assert a.read_text() == b.read_text()
    for x, y in zip(
        sorted((tmp_path / "a").glob("*.eml")), sorted((tmp_path / "b").glob("*.eml")), strict=True
    ):
        assert x.read_bytes() == y.read_bytes()
    labels = [json.loads(line) for line in a.read_text().splitlines()]
    assert len(labels) == 60 and {x["label"] for x in labels} == {"benign"}
    assert len({x["template"] for x in labels}) >= 6
    for item in labels:
        env = envelope.from_path(tmp_path / "a" / item["file"])
        cf = code_findings(env, BASELINES)
        assert not cf.red_flag and not injection_attempt(env), item
        assert "-1pm" not in env.part("body").text


def test_split_mbox_skips_large_messages(tmp_path):
    box = mailbox.mbox(str(tmp_path / "box"))
    for i, size in enumerate((10, 10, 5000)):
        msg = EmailMessage()
        msg["From"] = "x@y.example"
        msg["Subject"] = f"m{i}"
        msg.set_content("a" * size)
        box.add(msg)
    box.flush()
    box.close()
    r = split_mbox(
        tmp_path / "box", tmp_path / "out", label="phish", source="t", prefix="t", max_bytes=2000
    )
    assert (r.written, r.skipped_large, r.skipped_broken) == (2, 1, 0)
    items = load_corpus([tmp_path / "out"])
    assert [i.label for i in items] == ["phish", "phish"]
    assert all(envelope.from_path(i.path).kind.value == "email" for i in items)


def test_batch_measures_detection_and_cost(tmp_path, ledger):
    generate_benign(tmp_path / "benign", 30)
    _phish_corpus(tmp_path / "phish", 20)
    items = interleave(load_corpus([tmp_path / "benign"]), load_corpus([tmp_path / "phish"]))
    (tmp_path / "broken.eml").write_bytes(b"\xff\xfe\x00\x81\x92")
    from forgeember.corpus import CorpusItem

    items.append(CorpusItem(tmp_path / "broken.eml", "benign", "test"))
    ember = ContentEmber()
    stats = run_batch(items, _screen(ledger, ember), receipts_dir=tmp_path / "receipts", workers=4)
    assert stats.processed == 50 and stats.errors == 1
    assert (stats.tp, stats.fn, stats.fp, stats.tn) == (20, 0, 0, 30)
    assert str(stats.precision) == str(stats.recall) == "1.000"
    assert ember.calls == 50  # bulk mode: one triage call per plain email
    assert stats.cost_nusd == sum(c.cost_nusd for c in ledger.calls())
    assert stats.to_dict()["projected_10k_items_nusd"] == stats.cost_per_item_nusd * 10_000

    again = run_batch(items, _screen(ledger, ember), receipts_dir=tmp_path / "receipts", workers=4)
    assert again.skipped_resume == 50 and again.processed == 0
    assert ember.calls == 50  # resume never pays twice


def test_batch_stops_cleanly_at_the_stage_limit(tmp_path, make_ledger):
    config = BudgetConfig(
        grant_nusd=usd_to_nusd("10"),
        global_cap_nusd=usd_to_nusd("0.003"),
        stage_limits_nusd={"scale": usd_to_nusd("0.003")},
    )
    ledger = make_ledger(config)
    _phish_corpus(tmp_path / "phish", 40)
    stats = run_batch(
        load_corpus([tmp_path / "phish"]),
        _screen(ledger, ContentEmber()),
        receipts_dir=tmp_path / "r",
        workers=1,
    )
    assert stats.budget_stopped
    assert 0 < stats.processed < 40
    assert sum(c.cost_nusd for c in ledger.calls()) <= usd_to_nusd("0.003")


def test_bulk_mode_still_runs_risk_for_money_requests(ledger):
    env = envelope.from_path(ROOT / "fixtures" / "emails" / "em-002-ceo-impersonation.eml")

    class Money(ContentEmber):
        def post_json(self, path, body):
            name = body["text"]["format"]["name"]
            if name == "triage_result":
                answer = {
                    "category": "executive_request",
                    "summary": "s",
                    "needs_document_extraction": False,
                    "risk_signals": [],
                    "escalate": True,
                }
            else:
                answer = {"verdict": "likely_fraud", "signals": [], "rationale": "r"}
            return TransportResponse(200, responses_payload(text=json.dumps(answer)))

    result = _screen(ledger, Money(), skeptic_mode=SkepticMode.OFF)(env)  # batch default
    assert [c.agent_role for c in result.calls] == ["coordinator", "risk"]
    assert result.decision.risk.value == "high"


def test_legacy_charset_email_is_still_email():
    raw = (
        b"Return-Path: x@y.example\nFrom: Caf\xe9 <x@y.example>\nSubject: Rechnung\n\n"
        + b"Bitte \xfcberweisen Sie heute.\n"
    )
    assert envelope.sniff_mime(raw) == "message/rfc822"
    env = envelope.from_bytes(raw, source="legacy.eml")
    assert env.kind.value == "email"


def test_transient_errors_get_a_second_pass(tmp_path, ledger):
    _phish_corpus(tmp_path / "phish", 6)

    class Flaky(ContentEmber):
        def __init__(self):
            super().__init__()
            self.failed = set()

        def post_json(self, path, body):
            key = json.dumps(body["input"])
            if key not in self.failed:  # every item fails once with a retryable 503
                self.failed.add(key)
                return TransportResponse(
                    503, {"error": {"type": "server_error", "code": "backend_unavailable"}}
                )
            return super().post_json(path, body)

    slept = []
    stats = run_batch(
        load_corpus([tmp_path / "phish"]),
        _screen(ledger, Flaky()),
        receipts_dir=tmp_path / "r",
        workers=2,
        sleep=slept.append,
    )
    assert (stats.processed, stats.errors, stats.retried) == (6, 0, 6)
    assert slept == [60.0]
    failed = [c for c in ledger.calls() if c.outcome == "failed"]
    assert len(failed) == 6 and all(c.cost_nusd == c.reservation_nusd for c in failed)


def test_bulk_mode_caps_text_sent_to_the_model(ledger):
    msg = EmailMessage()
    msg["From"] = "a@b.example"
    msg["Subject"] = "long"
    msg.set_content("Start here. " + "filler " * 3000 + f"Please {PHISH_PHRASE}.")
    env = envelope.from_bytes(msg.as_bytes(), source="long.eml")
    seen = []

    class Spy(ContentEmber):
        def post_json(self, path, body):
            seen.append(json.dumps(body["input"]))
            return super().post_json(path, body)

    _screen(ledger, Spy())(env)
    assert "more characters not shown" in seen[0]
    assert PHISH_PHRASE not in seen[0]  # beyond the cap: the model never saw it


def test_broken_charset_parameter_still_parses():
    raw = (
        b"From: a@b.example\nSubject: x\nMIME-Version: 1.0\n"
        b'Content-Type: text/plain; charset="UTF-8Return-Path: bounce@c.example"\n\n'
        b"Your mailbox is full, click here.\n"
    )
    env = envelope.from_bytes(raw, source="broken.eml")
    assert "mailbox is full" in env.part("body").text


def test_truncated_output_gets_one_retry_with_double_budget(ledger):
    env = envelope.from_path(ROOT / "fixtures" / "emails" / "em-004-newsletter.eml")
    budgets = []

    class Truncating(ContentEmber):
        def post_json(self, path, body):
            budgets.append(body["max_output_tokens"])
            if len(budgets) == 1:
                return TransportResponse(200, responses_payload(status="incomplete", text="{"))
            return super().post_json(path, body)

    result = _screen(ledger, Truncating())(env)
    assert budgets == [2048, 4096]
    assert result.errors == [] and result.decision.risk.value == "low"


def test_permanent_api_errors_are_kept_as_incomplete_and_flagged(tmp_path, ledger):
    _phish_corpus(tmp_path / "phish", 3)

    class Refusing(ContentEmber):
        def post_json(self, path, body):
            return TransportResponse(
                400,
                {"error": {"type": "invalid_request_error", "code": "content_policy_violation"}},
            )

    slept = []
    stats = run_batch(
        load_corpus([tmp_path / "phish"]),
        _screen(ledger, Refusing()),
        receipts_dir=tmp_path / "r",
        sleep=slept.append,
    )
    assert (stats.processed, stats.incomplete, stats.errors, stats.retried) == (3, 3, 0, 0)
    assert slept == []  # permanent errors are never retried
    assert stats.tp == 3  # incomplete screening is MEDIUM: flagged for a human
    assert len(list((tmp_path / "r").glob("*.json"))) == 3


def test_attachment_the_api_cannot_process_falls_back_to_extracted_text(ledger):
    """Seen live: crafted phishing PDFs that make Meta return 503 on every attempt."""
    env = envelope.from_path(ROOT / "fixtures" / "emails" / "em-005-invoice-attached.eml")
    seen, bodies = [], []

    class PdfAllergic(ContentEmber):
        def post_json(self, path, body):
            blocks = [b for item in body["input"] for b in item["content"]]
            has_file = any(b["type"] == "input_file" for b in blocks)
            seen.append((body["text"]["format"]["name"], has_file))
            bodies.append(json.dumps(body["input"]))
            if has_file:
                return TransportResponse(
                    503, {"error": {"type": "server_error", "code": "backend_unavailable"}}
                )
            if body["text"]["format"]["name"] == "triage_result":
                return super().post_json(path, body)
            return TransportResponse(
                200,
                responses_payload(
                    text=json.dumps(
                        {
                            "is_invoice": False,
                            "vendor_name": None,
                            "vendor_email": None,
                            "invoice_number": None,
                            "invoice_date": None,
                            "due_date": None,
                            "currency": None,
                            "line_items": [],
                            "subtotal": None,
                            "tax": None,
                            "total": None,
                            "bank": None,
                            "claims_bank_change": False,
                            "evidence": [],
                        }
                    )
                ),
            )

    result = _screen(ledger, PdfAllergic())(env)
    assert result.media_fallback and result.errors == []
    assert seen[0] == ("triage_result", True) and seen[1] == ("triage_result", False)
    assert all(not has_file for _, has_file in seen[1:])  # no second paid failure
    assert "Invoice number: FS-2026-1203" in bodies[1]  # the PDF text layer, as text
    assert "could not process this file" in bodies[1]
    assert result.decision.risk.value != "low"
    assert any("could not process an attachment" in r for r in result.decision.reasons)
    assert [c.outcome for c in ledger.calls()].count("failed") == 1
