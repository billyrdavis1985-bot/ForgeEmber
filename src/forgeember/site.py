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


def _attack_items(paths: Paths) -> list[dict[str, Any]]:
    """The attack suite, shaped like fixture items (file relative to fixtures/)."""
    spec_path = paths.root / "fixtures" / "attacks" / "manifest.json"
    if not spec_path.exists():
        return []
    return [
        {
            "id": a["id"],
            "file": f"attacks/{a['file']}",
            "scenario": a["attack"],
            "attack": f"defense: {a['defense']}",
            "defense": a["defense"],
            "needs_ember": bool(a.get("needs_ember")),
            "expected": {
                "category": a["expected"]["category"],
                "risk": a["expected"]["min_risk"],
                "held": a["expected"]["held"],
            },
        }
        for a in _load(spec_path)["items"]
    ]


def _ordinary_items(paths: Paths) -> list[dict[str, Any]]:
    """The ordinary-mail set, shaped like fixture items (file relative to fixtures/)."""
    spec_path = paths.root / "fixtures" / "ordinary" / "manifest.json"
    if not spec_path.exists():
        return []
    return [
        {
            "id": o["id"],
            "file": f"ordinary/{o['file']}",
            "scenario": o["message"],
            "attack": None,
            "label": o["label"],
            "why": o["why"],
            "expected": {
                "category": "",
                "risk": "low" if o["label"] == "routine" else "medium",
                "held": [],
            },
        }
        for o in _load(spec_path)["items"]
    ]


def latest_report(reports_dir: Path, name: str) -> dict[str, Any] | None:
    files = sorted(reports_dir.glob(f"{name}-*.json"))
    return {**_load(files[-1]), "file": files[-1].name} if files else None


def latest_attacks(attacks_dir: Path) -> dict[str, Any] | None:
    files = sorted(attacks_dir.glob("attacks-*.json"))
    return {**_load(files[-1]), "file": files[-1].name} if files else None


def export_demo(paths: Paths) -> dict[str, Any]:
    """Copy receipts of fixture items from var/receipts into traces/demo/ (committable).

    Only fixtures are exported (fictional data). The newest receipt per fixture wins, so
    re-screening a fixture replaces its recorded trail.
    """
    items = [*_manifest(paths), *_attack_items(paths), *_ordinary_items(paths)]
    by_sha = {
        hashlib.sha256((paths.root / "fixtures" / i["file"]).read_bytes()).hexdigest(): i
        for i in items
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
        # the labeled fixtures still to record (attack receipts are exported when present)
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


def latest_scenario(reports_dir: Path, name: str) -> dict[str, Any] | None:
    """The newest recorded run of a scenario that checked hold states, preferring one where
    every step was as expected. Failed runs stay in traces/ as evidence; the page says which
    run it shows."""
    runs = []
    for path in sorted(reports_dir.glob(f"{name}-*.json")):
        report = _load(path)
        if report.get("name") == name and all("hold_checks" in s for s in report["steps"]):
            runs.append({**report, "file": path.name})
    passed = [r for r in runs if all(s["ok"] for s in r["steps"])]
    return (passed or runs or [None])[-1]


def case_steps(
    spec: dict[str, Any], report: dict[str, Any], receipts: dict[str, Any]
) -> list[dict[str, Any]]:
    """One timeline entry per scenario step: what arrived, what code did with it, and the
    state of the payment hold right after it, all from the recorded run."""
    steps: list[dict[str, Any]] = []
    first: dict[str, int] = {}  # fixture id -> the step it first arrived in (1-based)
    for n, want in enumerate(spec["steps"], start=1):
        first.setdefault(want["fixture"], n)

    def ref(fid: str) -> str | None:
        return ((receipts.get(fid) or {}).get("extraction") or {}).get("invoice_number")

    for n, (want, got) in enumerate(zip(spec["steps"], report["steps"], strict=True), start=1):
        fid = want["fixture"]
        r = receipts.get(fid) or {}
        again = first[fid] < n
        draft = (
            (r.get("handoff") or {}).get("draft") if got["linked_to"] and got["changes"] else None
        )
        steps.append(
            {
                "title": want.get("title") or fid,
                "fixture": fid,
                "again": again,
                "r": r,
                "linked_to": got["linked_to"],
                "changes": got["changes"],
                "note": got["note"],
                "linked_step": first.get(got["linked_to"] or ""),
                "holds": [
                    {
                        **h,
                        "label": f"The payment for invoice {ref(h['hold_of']) or h['hold_of']}"
                        + (f" (step {first[h['hold_of']]})" if len(got["hold_checks"]) > 1 else ""),
                    }
                    for h in got["hold_checks"]
                ],
                "draft": draft,
                "ok": got["ok"],
                "cost_nusd": got["cost_nusd"],
            }
        )
    return steps


def _build_cases(
    paths: Paths, out: Path, env: Any, receipts: dict[str, Any], files: dict[str, str]
) -> list[dict[str, Any]]:
    """A page per recorded scenario: the front door. Returns what the index links to."""
    cases = []
    (out / "cases").mkdir(exist_ok=True)
    specs = [_load(p) for p in (paths.root / "fixtures" / "scenarios").glob("*.json")]
    for spec in sorted(specs, key=lambda sp: (sp.get("order", 99), sp["name"])):
        report = latest_scenario(paths.root / "traces" / "scenarios", spec["name"])
        if report is None:
            continue
        steps = case_steps(spec, report, receipts)
        page = env.get_template("site_case.html").render(
            base="../",
            home="../index.html",
            htmx=False,
            repo=REPO_URL,
            spec=spec,
            report=report,
            steps=steps,
            files=files,
            passed=sum(s["ok"] for s in steps),
        )
        path = out / "cases" / f"{spec['name']}.html"
        path.write_text(page, encoding="utf-8")
        cases.append(
            {
                "name": spec["name"],
                "title": spec.get("title") or spec["name"],
                "lede": spec.get("lede", ""),
                "url": f"cases/{spec['name']}.html",
                "path": path,
                "passed": sum(s["ok"] for s in steps),
                "total": len(steps),
            }
        )
    return cases


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
    attack_rows = []
    written: list[Path] = []
    files: dict[str, str] = {}  # fixture id -> file name under files/
    attacks = latest_attacks(paths.root / "traces" / "attacks")
    scored = {a["id"]: a for a in (attacks or {}).get("items", [])}
    attack_ids = {a["id"] for a in _attack_items(paths)}
    ordinary = latest_report(paths.root / "traces" / "ordinary", "ordinary")
    ordinary_scored = {o["id"]: o for o in (ordinary or {}).get("items", [])}
    ordinary_ids = {o["id"] for o in _ordinary_items(paths)}
    ordinary_rows = []
    for item in [*_manifest(paths), *_attack_items(paths), *_ordinary_items(paths)]:
        r = receipts.get(item["id"])
        held = _held(r) if r else []
        match = (
            bool(r)
            and r.get("risk") == item["expected"]["risk"]
            and sorted(held) == sorted(item["expected"]["held"])
        )
        if item["id"] in attack_ids:
            attack_rows.append({**item, "r": r, "held": held, "score": scored.get(item["id"])})
        elif item["id"] in ordinary_ids:
            ordinary_rows.append(
                {**item, "r": r, "held": held, "score": ordinary_scored.get(item["id"])}
            )
        else:
            rows.append({**item, "r": r, "held": held, "match": match})
        src = paths.root / "fixtures" / item["file"]
        shutil.copy(src, out / "files" / src.name)
        files[item["id"]] = src.name
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

    cases = _build_cases(paths, out, env, receipts, files)
    written += [c["path"] for c in cases]
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
        cases=cases,
        attacks=attacks,
        attack_rows=attack_rows,
        ordinary=ordinary,
        ordinary_rows=ordinary_rows,
    )
    (out / "index.html").write_text(index, encoding="utf-8")
    written.append(out / "index.html")
    return written
