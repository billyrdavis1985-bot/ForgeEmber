"""Approval console (FastAPI + HTMX) and the keyless static replay site."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgeember import envelope
from forgeember.actions import HoldQueue, carry_out, write_receipt
from forgeember.baseline import BaselineStore
from forgeember.budget.ledger import assert_no_secrets
from forgeember.config import Paths
from forgeember.console.app import create_app
from forgeember.ember.client import EmberClient
from forgeember.pipeline import Screener
from forgeember.site import aggregate_scale, build_site, export_demo, pick_runs
from ideal_ember import IdealEmber

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path, ledger):
    """A tmp repo root with fixtures, screened by an ideal Ember: receipts and holds exist."""
    shutil.copytree(ROOT / "fixtures", tmp_path / "fixtures")
    paths = Paths(root=tmp_path)
    baselines = BaselineStore.load(paths.baselines)
    items = json.loads((paths.root / "fixtures" / "manifest.json").read_text())["items"]
    holds = HoldQueue(paths.holds_db)
    try:
        for item in items:
            env = envelope.from_path(paths.root / "fixtures" / item["file"])
            client = EmberClient(ledger=ledger, stage="demo", transport=IdealEmber(item, env))
            result = Screener(client=client, baselines=baselines).screen(env)
            outcome = carry_out(
                result.decision, item_id=env.id, holds=holds, notifications=paths.notifications
            )
            write_receipt(result.receipt(outcome), paths.receipts_dir)
    finally:
        holds.close()
    scale = paths.root / "traces" / "scale"
    scale.mkdir(parents=True)
    summary = {
        "processed": 100,
        "incomplete": 0,
        "cost_nusd": 27_000_000,
        "calls": 120,
        "elapsed_s": 60,
        "confusion": {"tp": 48, "fp": 1, "tn": 49, "fn": 2},
        "config": {"limit": None},
    }
    (scale / "scale-1.json").write_text(json.dumps(summary))
    (scale / "scale-0-pilot.json").write_text(json.dumps({**summary, "config": {"limit": 200}}))
    receipts = {
        **summary,
        "processed": 40,
        "confusion": {"tp": 0, "fp": 1, "tn": 39, "fn": 0},
        "by_label": {"receipt": 40},
        "accuracy": {
            "category": {"correct": 39, "labeled": 40, "rate": "0.975"},
            "total": {"correct": 34, "labeled": 38, "rate": "0.895"},
        },
        "config": {"limit": None, "sources": ["data/cord"]},
    }
    (scale / "scale-2-receipts.json").write_text(json.dumps(receipts))
    return paths


def test_console_lists_holds_and_records_named_decisions(repo):
    client = TestClient(create_app(repo))
    page = client.get("/")
    assert page.status_code == 200
    assert "Held for your approval" in page.text and "change_bank_details" in page.text
    assert 'hx-post="/holds/' in page.text

    assert client.post("/holds/1/approve", data={"decided_by": "Billy Davis"}).status_code == 403
    hx = {"HX-Request": "true"}
    assert client.post("/holds/1/approve", data={"decided_by": " "}, headers=hx).status_code == 400
    assert client.post("/holds/1/launch", data={"decided_by": "B"}, headers=hx).status_code == 404
    done = client.post("/holds/1/approve", data={"decided_by": "Billy Davis"}, headers=hx)
    assert done.status_code == 200 and "approved" in done.text and "Billy Davis" in done.text
    again = client.post("/holds/1/reject", data={"decided_by": "Billy Davis"}, headers=hx)
    assert again.status_code == 409  # a decided hold is never reopened
    assert "Recently decided" in client.get("/").text


def test_console_item_page_shows_the_whole_trail(repo):
    client = TestClient(create_app(repo))
    receipt_file = next(
        f
        for f in repo.receipts_dir.glob("*.json")
        if json.loads(f.read_text())["category"] == "bank_change_request"
    )
    r = json.loads(receipt_file.read_text())
    page = client.get(f"/items/{r['item_id']}")
    assert page.status_code == 200
    for section in (
        "What code found first",
        "What Ember proposed",
        "What code verified",
        "What policy decided",
        "What it cost",
    ):
        assert section in page.text
    assert "our bank details have changed" in page.text or "update your records" in page.text
    assert client.get("/items/itm_..%2F..%2Fetc").status_code == 404
    assert client.get("/items/not-an-item").status_code == 404
    assert client.get("/budget").status_code == 200


def test_demo_export_and_keyless_site(repo):
    run = export_demo(repo)
    assert len(run["items"]) == 13 and run["missing"] == []
    exported = json.loads((repo.root / "traces/demo/receipts/em-001.json").read_text())
    assert exported["source"] == "fixtures/emails/em-001-bank-change-lookalike.eml"

    # hostile text in a recorded receipt is shown as text, never executed
    p = repo.root / "traces/demo/receipts/em-004.json"
    r = json.loads(p.read_text())
    r["triage"]["summary"] = "<script>alert(1)</script>"
    p.write_text(json.dumps(r))

    out = repo.root / "site"
    written = build_site(repo, out)
    assert (out / "index.html") in written and len(list((out / "items").glob("*.html"))) == 13
    index = (out / "index.html").read_text()
    assert "Decision trails" in index and "Budget &amp; evidence" in index
    assert "96.0%" in index  # recall from the full-corpus summary only (48 / 50), not the pilot
    assert "real receipt photos screened" in index
    assert "89.5%" in index and "34 of 38 human-labeled" in index  # receipts, a separate card
    em004 = (out / "items/em-004.html").read_text()
    assert "<script>alert(1)</script>" not in em004 and "&lt;script&gt;" in em004
    assert "htmx" not in index  # the static site ships no JavaScript
    for f in out.rglob("*.html"):
        text = f.read_text()
        assert_no_secrets(text)
        assert "/var/" not in text and str(repo.root) not in text
    assert (out / "files/inv-001-fennimore.pdf").exists()


def test_scale_aggregate_ignores_pilot_runs_and_groups_by_corpus(tmp_path):
    d = tmp_path / "scale"
    d.mkdir()
    base = {
        "processed": 10,
        "incomplete": 0,
        "cost_nusd": 10,
        "calls": 10,
        "elapsed_s": 1,
        "confusion": {"tp": 5, "fp": 0, "tn": 5, "fn": 0},
    }
    mail = {"limit": None, "sources": ["data/nazario/eml", "data/benign-v1"]}
    (d / "scale-a.json").write_text(json.dumps({**base, "config": mail}))
    # a resume rerun of the same corpus, sources listed in another order
    rerun = {**mail, "sources": ["data/benign-v1", "data/nazario/eml"]}
    (d / "scale-b.json").write_text(json.dumps({**base, "config": rerun}))
    (d / "scale-c.json").write_text(json.dumps({**base, "config": {**mail, "limit": 200}}))
    receipts = {
        **base,
        "confusion": {"tp": 0, "fp": 1, "tn": 9, "fn": 0},
        "by_label": {"receipt": 10},
        "accuracy": {"total": {"correct": 8, "labeled": 9, "rate": "0.889"}},
        "config": {"limit": None, "sources": ["data/cord"]},
    }
    (d / "scale-d.json").write_text(json.dumps(receipts))
    (d / "scale-e.json").write_text(json.dumps(receipts))

    groups = aggregate_scale(d)
    email, cord = pick_runs(groups)
    assert len(groups) == 2
    assert email["processed"] == 20 and email["recall"] == 1.0
    assert cord["processed"] == 20 and cord["by_label"] == {"receipt": 20}
    assert cord["accuracy"]["total"] == {"correct": 16, "labeled": 18, "rate": 16 / 18}
    assert cord["false_positive_rate"] == 0.1
    assert aggregate_scale(tmp_path / "missing") == []
    assert pick_runs([]) == (None, None)
