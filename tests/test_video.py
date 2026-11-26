"""Video input (Gate D): Ember watches a screen recording; code checks what it shows."""

import json
from pathlib import Path

from forgeember import envelope
from forgeember.agents.inputs import item_content
from forgeember.baseline import BankStatus, BaselineStore
from forgeember.ember.client import EmberClient
from forgeember.handoff import next_steps
from forgeember.pipeline import Screener
from forgeember.policy import Risk
from forgeember.web import input_parts, jinja_env
from ideal_ember import IdealEmber

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BASELINES = BaselineStore.load(FIXTURES / "baselines.json")
ITEM = next(
    i for i in json.loads((FIXTURES / "manifest.json").read_text())["items"] if i["id"] == "vd-001"
)


def _screen(ledger):
    env = envelope.from_path(FIXTURES / ITEM["file"])
    ember = IdealEmber(ITEM, env)
    client = EmberClient(ledger=ledger, stage="reserve", transport=ember)
    return env, Screener(client=client, baselines=BASELINES).screen(env), ember


def test_video_goes_inline_as_mp4_and_its_cost_is_counted_first(ledger):
    env, _, ember = _screen(ledger)
    blocks = item_content(env)[0]["content"]
    [video] = [b for b in blocks if b["type"] == "input_video"]
    assert video["video_url"].startswith("data:video/mp4;base64,")
    assert "vd-001" not in json.dumps(blocks)  # neutral name: "video.mp4"
    paths = [p for p, _ in ember.calls]
    assert paths.count("/responses/input_tokens") == paths.count("/responses")  # each bounded


def test_a_portal_bank_change_on_video_is_held_and_checked_against_the_file(ledger):
    _, result, _ = _screen(ledger)
    assert result.video.payee_organization == "Marrowvale Couriers"
    assert result.extraction is None  # a video is not an invoice document
    assert result.facts.bank_status is BankStatus.CHANGED and result.facts.video
    assert result.decision.risk is Risk.HIGH
    assert [a.kind for a in result.decision.held] == ["change_bank_details"]
    reasons = " ".join(result.decision.reasons)
    assert "screen recording shows bank details that are not on file" in reasons
    assert "seen only in a screen recording" in reasons
    step = next_steps(result, BASELINES)["change_bank_details"]
    assert step.text.startswith("Call Marrowvale Couriers on 01632 960481")
    assert {c.name for c in result.checks} >= {"video.recording"}


def test_trail_plays_the_video_and_shows_what_ember_saw(ledger):
    env, result, _ = _screen(ledger)
    macro = jinja_env().get_template("_receipt.html").module.receipt
    html = str(macro(result.receipt(), input_parts(env)))
    assert '<video controls preload="none" width="640" src="data:video/mp4;base64,' in html
    assert "what Ember saw" in html and "<code>New account number: 55018273</code>" in html
