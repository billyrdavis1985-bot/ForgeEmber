"""Publish ledger numbers: snapshot JSON, README block, SVG chart, submission export."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forgeember.budget.ledger import (
    DEFAULT_CONFIG,
    BudgetConfig,
    Ledger,
    assert_no_secrets,
    effective_limits,
    read_events,
    read_trace,
)
from forgeember.budget.pricing import PRICING_CHECKED, PRICING_SOURCE_URL
from forgeember.budget.readme import extract_block, render_block, render_svg, replace_block
from forgeember.budget.report import snapshot, summarize
from forgeember.config import EMBER_MODEL, Paths


class PublishError(RuntimeError):
    pass


def snapshot_from_ledger(ledger: Ledger) -> dict[str, Any]:
    open_calls = ledger.calls(status="open")
    if open_calls:
        raise PublishError(
            f"{len(open_calls)} open reservation(s): settle them or run "
            "`forgeember budget expire-open` before publishing numbers"
        )
    summary = summarize(
        ledger.calls(),
        config=ledger.config,
        effective_limits=ledger.effective_stage_limits(),
        reconciliations=ledger.reconciliations(),
    )
    return snapshot(summary)


def snapshot_from_traces(paths: Paths, config: BudgetConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    """Recompute the snapshot from the committed JSONL traces alone (used by CI)."""
    transfers, recs = read_events(paths.events_trace)
    summary = summarize(
        read_trace(paths.calls_trace),
        config=config,
        effective_limits=effective_limits(config, transfers),
        reconciliations=recs,
    )
    return snapshot(summary)


def dump_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class PublishResult:
    changed: tuple[Path, ...]


def publish(paths: Paths, snap: dict[str, Any], *, check: bool = False) -> PublishResult:
    """Write (or, with ``check``, verify) snapshot, chart and README block."""
    targets: dict[Path, str] = {
        paths.snapshot: dump_json(snap),
        paths.chart: render_svg(snap),
    }
    readme_text = paths.readme.read_text(encoding="utf-8")
    targets[paths.readme] = replace_block(readme_text, render_block(snap))
    changed = tuple(
        p for p, text in targets.items() if not p.exists() or p.read_text(encoding="utf-8") != text
    )
    if not check:
        for p in changed:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(targets[p], encoding="utf-8")
    return PublishResult(changed=changed)


def readme_matches_snapshot(paths: Paths) -> bool:
    snap = json.loads(paths.snapshot.read_text(encoding="utf-8"))
    readme_block = extract_block(paths.readme.read_text(encoding="utf-8"))
    return readme_block == render_block(snap)


def export_submission(ledger: Ledger, paths: Paths) -> tuple[Path, Path]:
    """Write traces/submission-trace.jsonl and traces/summary.json for the hackathon."""
    snap = snapshot_from_ledger(ledger)
    calls = sorted(
        (c for c in ledger.calls(status="settled")), key=lambda c: (c.settled_at, c.call_id)
    )
    lines = [json.dumps(c.to_trace(), separators=(",", ":")) for c in calls]
    body = "".join(line + "\n" for line in lines)
    summary = {
        "schema": "forgeember.submission-summary.v1",
        "model": EMBER_MODEL,
        "pricing_source": PRICING_SOURCE_URL,
        "pricing_checked": PRICING_CHECKED,
        "budget": snap,
        "reconciliations": [
            {
                "ts": r.ts,
                "meta_total_nusd": r.meta_total_nusd,
                "ledger_total_nusd": r.ledger_total_nusd,
                "difference_nusd": r.difference_nusd,
                "period_start": r.period_start,
                "period_end": r.period_end,
                "note": r.note,
            }
            for r in ledger.reconciliations()
        ],
    }
    summary_text = dump_json(summary)
    assert_no_secrets(body)
    assert_no_secrets(summary_text)
    paths.submission_trace.parent.mkdir(parents=True, exist_ok=True)
    paths.submission_trace.write_text(body, encoding="utf-8")
    paths.submission_summary.write_text(summary_text, encoding="utf-8")
    return paths.submission_trace, paths.submission_summary
