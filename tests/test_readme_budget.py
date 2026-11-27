"""CI gate: README budget numbers must match the ledger snapshot and the committed trace."""

import json
from pathlib import Path

import pytest

from conftest import req
from forgeember.budget.ledger import Outcome, Usage
from forgeember.budget.publish import (
    PublishError,
    publish,
    readme_matches_snapshot,
    snapshot_from_ledger,
    snapshot_from_traces,
)
from forgeember.budget.readme import (
    END_MARKER,
    START_MARKER,
    MarkerError,
    extract_block,
    render_block,
    render_svg,
    replace_block,
)
from forgeember.config import Paths

REPO = Paths(root=Path(__file__).resolve().parents[1])


def test_committed_readme_matches_committed_snapshot():
    assert readme_matches_snapshot(REPO), (
        "README budget block is stale: run `uv run python scripts/render_budget_readme.py`"
    )


def test_committed_chart_matches_committed_snapshot():
    snap = json.loads(REPO.snapshot.read_text(encoding="utf-8"))
    assert REPO.chart.read_text(encoding="utf-8") == render_svg(snap)


def test_committed_snapshot_matches_committed_trace():
    committed = json.loads(REPO.snapshot.read_text(encoding="utf-8"))
    assert committed == snapshot_from_traces(REPO), (
        "traces/budget-snapshot.json disagrees with traces/calls.jsonl"
    )


def _fake_repo(tmp_path: Path) -> Paths:
    paths = Paths(root=tmp_path)
    paths.readme.write_text(f"# X\n\nintro\n\n{START_MARKER}\nold\n{END_MARKER}\n\ntail\n")
    return paths


def test_publish_round_trip_with_live_numbers(tmp_path, make_ledger):
    paths = _fake_repo(tmp_path)
    ledger = make_ledger()
    u = Usage(input_tokens=3000, cached_input_tokens=2500, output_tokens=500, reasoning_tokens=200)
    for i in range(3):
        ledger.settle(ledger.reserve(req(item_id=f"inv-{i}")), outcome=Outcome.OK, usage=u)
    snap = snapshot_from_ledger(ledger)
    result = publish(paths, snap)
    assert set(result.changed) == {paths.snapshot, paths.chart, paths.readme}
    text = paths.readme.read_text()
    assert text.startswith("# X\n\nintro\n\n") and text.endswith("\n\ntail\n")
    assert readme_matches_snapshot(paths)
    assert publish(paths, snap, check=True).changed == ()
    # A hand edit to the README is detected.
    paths.readme.write_text(text.replace("Items screened | 3", "Items screened | 30000"))
    assert not readme_matches_snapshot(paths)
    assert publish(paths, snap, check=True).changed == (paths.readme,)


def test_snapshot_from_trace_equals_snapshot_from_ledger(tmp_path, make_ledger):
    ledger = make_ledger()
    u = Usage(input_tokens=500, cached_input_tokens=100, output_tokens=50, reasoning_tokens=10)
    ledger.settle(ledger.reserve(req(item_id="x")), outcome=Outcome.OK, usage=u)
    ledger.settle(
        ledger.reserve(req(item_id="y", stage="eval")), outcome=Outcome.FAILED, usage=None
    )
    ledger.reconcile(meta_total_nusd=1, note="test")
    paths = Paths(root=tmp_path / "r")
    paths.calls_trace.parent.mkdir(parents=True)
    paths.calls_trace.write_text(ledger.trace_path.read_text())
    paths.events_trace.write_text(ledger.events_path.read_text())
    assert snapshot_from_traces(paths) == snapshot_from_ledger(ledger)


def test_publish_refuses_with_open_reservations(make_ledger):
    ledger = make_ledger()
    ledger.reserve(req())
    with pytest.raises(PublishError):
        snapshot_from_ledger(ledger)


def test_block_shows_zero_dollars_when_empty(make_ledger):
    block = render_block(snapshot_from_ledger(make_ledger()))
    assert "| Total spent | **$0.00** of the $10.00 limit |" in block
    assert "| Hard cap enforced in code | $9.00 (headroom **$9.00**) |" in block


def test_marker_errors():
    with pytest.raises(MarkerError):
        replace_block("no markers", "x")
    with pytest.raises(MarkerError):
        replace_block(f"{START_MARKER}{END_MARKER}{START_MARKER}{END_MARKER}", "x")
    with pytest.raises(MarkerError):
        extract_block("nothing")


def test_export_submission_writes_trace_and_summary(tmp_path, make_ledger):
    from forgeember.budget.publish import export_submission

    ledger = make_ledger()
    u = Usage(input_tokens=800, cached_input_tokens=600, output_tokens=90, reasoning_tokens=40)
    for i in range(2):
        ledger.settle(ledger.reserve(req(item_id=f"e-{i}")), outcome=Outcome.OK, usage=u)
    ledger.reconcile(meta_total_nusd=1, note="usage page 2026-11-23")
    paths = Paths(root=tmp_path)
    trace, summary = export_submission(ledger, paths)
    lines = [json.loads(x) for x in trace.read_text().splitlines()]
    assert len(lines) == 2 and all(x["model"] == "muse-ember-1.0" for x in lines)
    data = json.loads(summary.read_text())
    assert data["budget"]["calls_settled"] == 2
    assert data["pricing_source"].startswith("https://dev.meta.ai/")
    assert data["reconciliations"][0]["note"] == "usage page 2026-11-23"
