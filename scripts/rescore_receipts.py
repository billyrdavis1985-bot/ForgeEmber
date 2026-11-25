"""Rescore saved batch outputs against their known answers with the current scorer. No API calls.

    uv run python scripts/rescore_receipts.py         # CORD receipts in var/scale/receipts
    uv run python scripts/rescore_receipts.py --receipts-dir var/scale/receipts-blinded

Reads every receipt that carries ``corpus.expected`` (written by ``forgeember batch``), scores
the final route and the extracted total again, and writes an aggregate-only sidecar,
traces/scale/rescore-<stamp>.json. The replay site uses it in place of the run's original
accuracy; the run summary itself is never edited. Individual items are never written out
(public-corpus results are shown only as aggregates, docs/DATA.md).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from forgeember.batch.runner import AMOUNT_SCORER, amounts_match
from forgeember.config import Paths, find_root


def rescore(receipts_dir: Path, corpus_prefix: str) -> dict:
    tally = {"category": [0, 0], "total": [0, 0]}
    flips = {"right_to_wrong": 0, "wrong_to_right": 0}
    seen = 0
    for f in sorted(receipts_dir.glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        corpus = r.get("corpus") or {}
        expected = corpus.get("expected") or {}
        if not expected or not str(corpus.get("source", "")).startswith(corpus_prefix):
            continue
        seen += 1
        if "category" in expected:
            tally["category"][0] += r.get("category") == expected["category"]
            tally["category"][1] += 1
        if "total" in expected:
            found = (r.get("extraction") or {}).get("total")
            ok = amounts_match(found, expected["total"])
            tally["total"][0] += ok
            tally["total"][1] += 1
            before = (corpus.get("matched") or {}).get("total")
            if before is True and not ok:
                flips["right_to_wrong"] += 1
            elif before is False and ok:
                flips["wrong_to_right"] += 1
    return {
        "receipts": seen,
        "accuracy": {name: {"correct": ok, "labeled": n} for name, (ok, n) in tally.items() if n},
        "total_changes_vs_original_scorer": flips,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts-dir", type=Path, help="default: var/scale/receipts")
    parser.add_argument("--corpus", default="cord-v2", help="corpus.source prefix to rescore")
    parser.add_argument("--sources", nargs="+", default=["data/cord"], help="the run's sources")
    args = parser.parse_args(argv)
    paths = Paths(root=find_root())
    receipts_dir = args.receipts_dir or paths.root / "var" / "scale" / "receipts"
    result = rescore(receipts_dir, args.corpus)
    if not result["receipts"]:
        print(f"error: no {args.corpus} receipts with known answers in {receipts_dir}")
        return 1
    out_dir = paths.root / "traces" / "scale"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"rescore-{stamp}.json"
    doc = {"schema": "forgeember.rescore.v1", "scorer": AMOUNT_SCORER, "sources": args.sources}
    out.write_text(json.dumps({**doc, **result}, indent=2) + "\n", encoding="utf-8")
    for name, a in result["accuracy"].items():
        print(f"{name}: {a['correct']}/{a['labeled']} ({a['correct'] / a['labeled']:.1%})")
    print(f"changes vs. the original scorer: {result['total_changes_vs_original_scorer']}")
    print(f"wrote {out.relative_to(paths.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
