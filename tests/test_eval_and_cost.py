"""Cost fixes from the first live run, and the Gate A evaluation harness."""

import json
from pathlib import Path

from forgeember import envelope
from forgeember.agents.base import (
    IMAGE_TOKENS,
    PDF_TOKENS_PER_PAGE,
    bounded_estimator,
    media_block_bound,
)
from forgeember.agents.inputs import item_content
from forgeember.baseline import BaselineStore
from forgeember.cli import main
from forgeember.ember.client import EmberClient
from forgeember.evaluate import evaluate, load_manifest
from forgeember.pipeline import Screener
from ideal_ember import IdealEmber

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")
ITEMS = load_manifest(FIXTURES / "manifest.json")


def _screen_factory(ledger, ember_cls=IdealEmber):
    def screen(item, env):
        client = EmberClient(ledger=ledger, stage="eval", transport=ember_cls(item, env))
        return Screener(client=client, baselines=BASELINES).screen(env)

    return screen


def test_static_bounds_cover_the_measured_pdf_cost():
    env = envelope.from_path(FIXTURES / "invoices/inv-001-fennimore.pdf")
    [block] = [b for b in item_content(env)[0]["content"] if b["type"] == "input_file"]
    bound = media_block_bound(block)
    assert bound >= 2_450 * 1.5  # first live run: ~2,450 tokens for this one-page PDF
    assert bound <= PDF_TOKENS_PER_PAGE + 2_000  # one page: no 200K outer bound any more
    assert (
        media_block_bound({"type": "input_image", "image_url": "data:image/png;base64,AA"})
        == IMAGE_TOKENS
    )
    assert media_block_bound({"type": "input_audio", "input_audio": {}}) is None


def test_pdfs_and_images_need_no_count_calls(ledger):
    item = next(i for i in ITEMS if i["id"] == "inv-003")  # the scanned PNG
    env = envelope.from_path(FIXTURES / item["file"])
    ember = IdealEmber(item, env)
    Screener(
        client=EmberClient(ledger=ledger, stage="eval", transport=ember), baselines=BASELINES
    ).screen(env)
    assert all(path == "/responses" for path, _ in ember.calls)


def test_audio_still_uses_the_count_endpoint(ledger):
    class Counter:
        def __init__(self):
            self.paths = []

        def post_json(self, path, body):
            from forgeember.ember.client import TransportResponse

            self.paths.append(path)
            return TransportResponse(200, {"object": "response.input_tokens", "input_tokens": 900})

    t = Counter()
    client = EmberClient(ledger=ledger, stage="dev", transport=t)
    items = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "x"},
                {"type": "input_audio", "input_audio": {"data": "AA", "format": "wav"}},
            ],
        }
    ]
    est = bounded_estimator(
        client, input=items, instructions="i", fmt={}, role="risk", item_id=None, input_kind="audio"
    )
    assert t.paths == ["/responses/input_tokens"] and est >= 900


def test_routine_invoice_does_not_escalate_even_if_triage_says_so(ledger):
    item = next(i for i in ITEMS if i["id"] == "inv-001")
    env = envelope.from_path(FIXTURES / item["file"])

    class EagerTriage(IdealEmber):  # the v1 prompt escalated every invoice
        def triage_result(self, body):
            return {**super().triage_result(body), "escalate": True}

    client = EmberClient(ledger=ledger, stage="eval", transport=EagerTriage(item, env))
    result = Screener(client=client, baselines=BASELINES).screen(env)
    assert [c.agent_role for c in result.calls] == ["coordinator", "document"]
    assert result.decision.risk.value == "low"


def test_eval_with_a_correct_model_passes_gate_a(ledger):
    report = evaluate(
        ITEMS,
        fixtures_dir=FIXTURES,
        screen=_screen_factory(ledger),
        ledger=ledger,
        skeptic_mode="escalated",
        stage="eval",
    )
    d = report.to_dict()
    assert d["items_scored"] == 23  # 7 invoices, 8 emails, 7 voicemails, 1 video
    assert (
        d["routing_accuracy"]
        == d["field_accuracy"]
        == d["risk_accuracy"]
        == d["held_accuracy"]
        == "1.000"
    )
    assert report.gate_a
    assert report.cost_nusd == sum(c.cost_nusd for c in ledger.calls()) > 0
    assert set(report.cost_by_kind()) == {"pdf", "image", "email", "audio", "video"}
    assert "Gate A (>= 90% routing and fields): PASS" in report.render_text()
    json.dumps(d, default=str)


def test_eval_scores_wrong_answers(ledger):
    class Sloppy(IdealEmber):
        def triage_result(self, body):
            return {**super().triage_result(body), "category": "other"}

        def invoice_extraction(self, body):
            return {**super().invoice_extraction(body), "invoice_number": "WRONG-1"}

    items = [i for i in ITEMS if i["id"] in ("em-003", "em-004", "inv-001")]
    report = evaluate(
        items,
        fixtures_dir=FIXTURES,
        screen=_screen_factory(ledger, Sloppy),
        ledger=ledger,
        skeptic_mode="escalated",
        stage="eval",
    )
    by_id = {i.id: i for i in report.items}
    assert not by_id["em-003"].routing_ok and not by_id["em-004"].routing_ok
    assert by_id["inv-001"].routing_ok  # code forces a verified invoice to "invoice"
    assert by_id["inv-001"].fields["invoice_number"] is False
    assert not report.gate_a


def test_eval_cli_refuses_without_live(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('name = "forgeember"\n')
    code = main(
        [
            "--root",
            str(tmp_path),
            "eval",
            "--stage",
            "eval",
            "--ids",
            "em-004",
            "--manifest",
            str(FIXTURES / "manifest.json"),
            "--baselines",
            str(FIXTURES / "baselines.json"),
        ]
    )
    assert code == 1 and "live" in capsys.readouterr().err
