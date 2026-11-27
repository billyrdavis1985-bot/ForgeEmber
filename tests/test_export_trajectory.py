"""scripts/export_usage_trajectory.py: totals, daily buckets and cumulative series."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "export_usage_trajectory", ROOT / "scripts/export_usage_trajectory.py"
)
trajectory = importlib.util.module_from_spec(spec)
sys.modules["export_usage_trajectory"] = trajectory
spec.loader.exec_module(trajectory)


def _line(ts, cost_nusd, input_tokens, output_tokens, stage="dev", outcome="ok"):
    return json.dumps(
        {
            "ts": ts,
            "stage": stage,
            "agent_role": "triage",
            "model": "muse-ember-1.0",
            "outcome": outcome,
            "cost_nusd": cost_nusd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    )


def test_export_writes_totals_daily_and_series(tmp_path):
    trace = tmp_path / "calls.jsonl"
    trace.write_text(
        "\n".join(
            [
                _line("2026-11-23T10:00:00.000Z", 100_000, 100, 10),
                _line("2026-11-23T11:00:00.000Z", 200_000, 200, 20, outcome="failed"),
                _line("2026-11-24T10:00:00.000Z", 300_000, 300, 30, stage="scale"),
            ]
        )
        + "\n"
    )
    out = tmp_path / "usage-trajectory.json"
    assert trajectory.main(["--trace", str(trace), "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == "forgeember.usage-trajectory.v1"
    assert doc["calls"] == 3
    assert doc["totals"] == {
        "cost_nusd": 600_000,
        "cost_usd": 0.0006,
        "input_tokens": 600,
        "output_tokens": 60,
    }
    assert [(d["date"], d["calls"], d["cost_nusd"]) for d in doc["daily"]] == [
        ("2026-11-23", 2, 300_000),
        ("2026-11-24", 1, 300_000),
    ]
    assert [p["cumulative_cost_nusd"] for p in doc["series"]] == [100_000, 300_000, 600_000]
    assert doc["by_outcome"] == {"failed": 1, "ok": 2}
    assert doc["by_stage"]["scale"]["calls"] == 1
    assert doc["source"]["trace"] == "traces/calls.jsonl"
