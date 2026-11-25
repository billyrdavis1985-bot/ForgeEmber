"""Keyless replay site, built only from committed artifacts (no model calls, no secrets).

Inputs: traces/demo/ (receipts of a recorded live run over the fixtures), traces/scale/
(run summaries, aggregated per corpus), traces/eval/ (Gate A reports),
traces/budget-snapshot.json, the budget chart and fixtures/. Output: a static folder (default
site/) for Cloudflare Pages or any static host. Fixtures are fictional; scale-run items appear
only as aggregates.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from forgeember import envelope
from forgeember.config import Paths
from forgeember.web import STATIC_DIR, input_parts, jinja_env

REPO_URL = "https://github.com/billyrdavis1985-bot/ForgeEmber"
DEMO_SCHEMA = "forgeember.demo-run.v1"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest(paths: Paths) -> list[dict[str, Any]]:
    return _load(paths.root / "fixtures" / "manifest.json")["items"]


def export_demo(paths: Paths) -> dict[str, Any]:
    """Copy receipts of fixture items from var/receipts into traces/demo/ (committable).

    Only fixtures are exported (fictional data). The newest receipt per fixture wins, so
    re-screening a fixture replaces its recorded trail.
    """
    by_sha = {
        hashlib.sha256((paths.root / "fixtures" / i["file"]).read_bytes()).hexdigest(): i
        for i in _manifest(paths)
    }
    out = paths.root / "traces" / "demo" / "receipts"
    out.mkdir(parents=True, exist_ok=True)
    exported: dict[str, dict[str, Any]] = {}
    for f in sorted(paths.receipts_dir.glob("*.json")) if paths.receipts_dir.exists() else []:
        r = _load(f)
        item = by_sha.get(r.get("sha256"))
        if item is None:
            continue
        r = {**r, "source": f"fixtures/{item['file']}", "fixture_id": item["id"]}
        (out / f"{item['id']}.json").write_text(
            json.dumps(r, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        exported[item["id"]] = r
    run = {
        "schema": DEMO_SCHEMA,
        "items": sorted(exported),
        "missing": sorted(i["id"] for i in _manifest(paths) if i["id"] not in exported),
        "cost_nusd": sum(r.get("cost_nusd") or 0 for r in exported.values()),
        "prompt_versions": sorted(
            {
                c["prompt_version"]
                for r in exported.values()
                for c in r.get("calls", [])
                if c.get("prompt_version")
            }
        ),
    }
    (paths.root / "traces" / "demo" / "run.json").write_text(
        json.dumps(run, indent=2) + "\n", encoding="utf-8"
    )
    return run


def _aggregate(sources: tuple[str, ...], runs: list[dict[str, Any]]) -> dict[str, Any]:
    agg: dict[str, Any] = {
        k: 0 for k in ("processed", "incomplete", "cost_nusd", "calls", "elapsed_s")
    }
    tp = fp = tn = fn = 0
    by_label: dict[str, int] = {}
    scored: dict[str, list[int]] = {}
    for r in runs:
        for k in agg:
            agg[k] += r.get(k) or 0
        c = r["confusion"]
        tp, fp, tn, fn = tp + c["tp"], fp + c["fp"], tn + c["tn"], fn + c["fn"]
        for label, n in (r.get("by_label") or {}).items():
            by_label[label] = by_label.get(label, 0) + n
        for name, a in (r.get("accuracy") or {}).items():
            tally = scored.setdefault(name, [0, 0])
            tally[0] += a["correct"]
            tally[1] += a["labeled"]
    agg.update(
        sources=list(sources),
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        positives=tp + fn,
        negatives=fp + tn,
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
        false_positive_rate=fp / (fp + tn) if fp + tn else None,
        cost_per_1k_nusd=agg["cost_nusd"] * 1000 // max(agg["processed"], 1),
        by_label=by_label,
        accuracy={
            name: {"correct": ok, "labeled": n, "rate": ok / n if n else None}
            for name, (ok, n) in sorted(scored.items())
        },
    )
    return agg


def aggregate_scale(scale_dir: Path) -> list[dict[str, Any]]:
    """One aggregate per corpus (``config.sources``), each summing its full-corpus runs: the
    main run plus its resume reruns. Pilot runs (``config.limit`` set) are left out.

    A ``rescore-*.json`` sidecar (scripts/rescore_receipts.py) replaces a corpus's accuracy
    with a rescore of its saved outputs under a newer scorer; the newest one wins. Run
    summaries themselves are never edited."""
    runs = [_load(p) for p in sorted(scale_dir.glob("scale-*.json"))] if scale_dir.exists() else []
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for r in runs:
        config = r.get("config") or {}
        if config.get("limit") is None:
            groups.setdefault(tuple(sorted(config.get("sources") or [])), []).append(r)
    aggs = [_aggregate(sources, group) for sources, group in groups.items()]
    rescores = [_load(p) for p in sorted(scale_dir.glob("rescore-*.json"))] if aggs else []
    for agg in aggs:
        for rs in rescores:  # sorted by timestamp: the last match is the newest
            if sorted(rs.get("sources") or []) == agg["sources"]:
                agg["accuracy"] = {
                    name: {**a, "rate": a["correct"] / a["labeled"] if a["labeled"] else None}
                    for name, a in rs["accuracy"].items()
                }
                agg["scorer"] = rs.get("scorer")
    return aggs


def pick_runs(groups: list[dict[str, Any]]) -> tuple[dict | None, dict | None]:
    """(phishing-detection aggregate, receipt-photo aggregate) for the results section."""
    email = next((g for g in groups if g["positives"]), None)
    receipts = next((g for g in groups if g["by_label"].get("receipt")), None)
    return email, receipts


def latest_eval(eval_dir: Path) -> dict[str, Any] | None:
    """The newest Gate A report (names start with a UTC timestamp, so they sort by time)."""
    files = sorted(eval_dir.glob("eval-*.json"))
    if not files:
        return None
    e = _load(files[-1])
    for k in ("routing_accuracy", "field_accuracy", "risk_accuracy", "held_accuracy"):
        e[k] = "n/a" if e.get(k) is None else f"{float(e[k]) * 100:.0f}%"
    return e


def _held(r: dict[str, Any]) -> list[str]:
    return [a["kind"] for a in r.get("actions", []) if a.get("disposition") == "hold"]


def build_site(paths: Paths, out: Path) -> list[Path]:
    env = jinja_env()
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    (out / "items").mkdir()
    (out / "files").mkdir()
    (out / "assets").mkdir()
    shutil.copy(STATIC_DIR / "style.css", out / "static" / "style.css")
    if paths.chart.exists():
        shutil.copy(paths.chart, out / "assets" / "budget-chart.svg")

    demo_dir = paths.root / "traces" / "demo"
    run = _load(demo_dir / "run.json") if (demo_dir / "run.json").exists() else None
    receipts = (
        {p.stem: _load(p) for p in sorted((demo_dir / "receipts").glob("*.json"))}
        if (demo_dir / "receipts").exists()
        else {}
    )
    snap = _load(paths.snapshot) if paths.snapshot.exists() else None
    scale, receipt_run = pick_runs(aggregate_scale(paths.root / "traces" / "scale"))
    evalr = latest_eval(paths.root / "traces" / "eval")

    by_item = {r["item_id"]: fid for fid, r in receipts.items() if r.get("item_id")}
    links = {item_id: f"../items/{fid}.html" for item_id, fid in by_item.items()}
    rows = []
    written: list[Path] = []
    for item in _manifest(paths):
        r = receipts.get(item["id"])
        held = _held(r) if r else []
        match = (
            bool(r)
            and r.get("risk") == item["expected"]["risk"]
            and sorted(held) == sorted(item["expected"]["held"])
        )
        rows.append({**item, "r": r, "held": held, "match": match})
        src = paths.root / "fixtures" / item["file"]
        shutil.copy(src, out / "files" / src.name)
        env_ = envelope.from_path(src)
        urls = {p.name: f"../files/{src.name}" for p in env_.parts}
        later = [
            {
                "url": f"../items/{fid}.html",
                "label": f"{fid}: {(other.get('triage') or {}).get('summary') or fid}",
                "note": other["followup"]["note"],
                "changes": other["followup"]["changes"],
            }
            for fid, other in receipts.items()
            if r and (other.get("followup") or {}).get("hold_item_id") == r.get("item_id")
        ]
        page = env.get_template("site_item.html").render(
            base="../",
            home="../index.html",
            htmx=False,
            item=item,
            r=r,
            held=held,
            parts=input_parts(env_, file_urls=urls),
            links=links,
            later=later,
        )
        path = out / "items" / f"{item['id']}.html"
        path.write_text(page, encoding="utf-8")
        written.append(path)

    recorded = sum(1 for row in rows if row["r"])
    demo_note = (
        f"Recorded live run: {recorded} of {len(rows)} fixtures, "
        f"{', '.join(run['prompt_versions'])}."
        if run and recorded
        else "No live demo run recorded yet: run forgeember screen on the fixtures, then "
        "forgeember demo export."
    )
    index = env.get_template("site_index.html").render(
        base="",
        home="index.html",
        htmx=False,
        repo=REPO_URL,
        scale=scale,
        receipts=receipt_run,
        evalr=evalr,
        snap=snap,
        fixtures=rows,
        demo_note=demo_note,
    )
    (out / "index.html").write_text(index, encoding="utf-8")
    written.append(out / "index.html")
    return written
