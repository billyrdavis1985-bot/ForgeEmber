"""Keyless replay site, built only from committed artifacts (no model calls, no secrets).

Inputs: traces/demo/ (receipts of a recorded live run over the fixtures), traces/scale/
(run summaries), traces/eval/ (Gate A reports), traces/budget-snapshot.json, the budget
chart and fixtures/. Output: a static folder (default site/) for Cloudflare Pages or any
static host. Fixtures are fictional; scale-run items appear only as aggregates.
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


def aggregate_scale(scale_dir: Path) -> dict[str, Any] | None:
    """Sum full-corpus runs (config.limit is null): the main run plus its resume reruns."""
    runs = [_load(p) for p in sorted(scale_dir.glob("scale-*.json"))] if scale_dir.exists() else []
    runs = [r for r in runs if (r.get("config") or {}).get("limit") is None]
    if not runs:
        return None
    agg: dict[str, Any] = {
        k: 0 for k in ("processed", "incomplete", "cost_nusd", "calls", "elapsed_s")
    }
    tp = fp = tn = fn = 0
    for r in runs:
        for k in agg:
            agg[k] += r.get(k) or 0
        c = r["confusion"]
        tp, fp, tn, fn = tp + c["tp"], fp + c["fp"], tn + c["tn"], fn + c["fn"]
    agg.update(
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        positives=tp + fn,
        negatives=fp + tn,
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
        cost_per_1k_nusd=agg["cost_nusd"] * 1000 // max(agg["processed"], 1),
    )
    return agg


def latest_eval(eval_dir: Path) -> dict[str, Any] | None:
    files = sorted(eval_dir.glob("eval-*-off.json")) or sorted(eval_dir.glob("eval-*.json"))
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
    scale = aggregate_scale(paths.root / "traces" / "scale")
    evalr = latest_eval(paths.root / "traces" / "eval")

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
        page = env.get_template("site_item.html").render(
            base="../",
            home="../index.html",
            htmx=False,
            item=item,
            r=r,
            held=held,
            parts=input_parts(env_, file_urls=urls),
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
        evalr=evalr,
        snap=snap,
        fixtures=rows,
        demo_note=demo_note,
    )
    (out / "index.html").write_text(index, encoding="utf-8")
    written.append(out / "index.html")
    return written
