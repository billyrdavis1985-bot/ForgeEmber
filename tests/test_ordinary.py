"""The ordinary-mail set (fixtures/ordinary): frozen fixtures and labels, and the scoring.

Deliberately no offline run of the set itself: the labels were committed before any run, and
the first measurement is the live one (traces/ordinary/), so nothing is tuned to it."""

import json
from pathlib import Path

from forgeember.cli import _cmd_ordinary, build_parser
from forgeember.ordinary import score, summarize
from forgeember.ordinarygen import generate_ordinary

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "fixtures" / "ordinary"
SPEC = json.loads((SUITE / "manifest.json").read_text())


def test_the_set_is_frozen_labeled_and_reproducible(tmp_path):
    assert SPEC["frozen"] == "2026-11-26" and "before any run" in SPEC["rule"]
    labels = [i["label"] for i in SPEC["items"]]
    assert len(labels) == 25 and labels.count("routine") == 18 and labels.count("check") == 7
    assert all(i["why"] and i["message"] for i in SPEC["items"])
    for path in generate_ordinary(tmp_path, audio=False):
        assert path.read_bytes() == (SUITE / path.name).read_bytes(), path.name


def _receipt(risk, reasons=(), cost=1000):
    return {"risk": risk, "reasons": list(reasons), "actions": [], "cost_nusd": cost}


def test_scoring_counts_interruptions_for_nothing_and_missed_checks():
    routine = {"id": "r", "message": "m", "label": "routine"}
    check = {"id": "c", "message": "m", "label": "check"}
    rows = [
        score(routine, _receipt("low")),
        score(routine, _receipt("medium", ["amount could not be read"])),
        score(check, _receipt("high")),
        score(check, _receipt("low")),
    ]
    assert [r["ok"] for r in rows] == [True, False, True, False]
    assert rows[1]["problems"] == [
        "unnecessary interruption (risk medium: amount could not be read)"
    ]
    assert rows[3]["problems"] == ["missed check: rated LOW"]
    report = summarize(rows)
    assert (report["unnecessary_interruptions"], report["routine"]) == (1, 2)
    assert (report["missed_checks"], report["check"]) == (1, 2)
    assert report["cost_per_100_nusd"] == 100_000  # 4,000 nUSD over 4 messages, per 100


def test_the_command_is_wired_and_needs_live_to_spend():
    args = build_parser().parse_args(["ordinary", "--stage", "eval"])
    assert args.func is _cmd_ordinary and args.live is False
