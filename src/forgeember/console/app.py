"""Approval console: a local FastAPI + HTMX app over the hold queue, receipts and ledger.

Run with ``forgeember console`` (binds to 127.0.0.1 only). Approving a hold only records the
decision with your name. ForgeEmber never pays, sends or changes bank details itself.
Every POST needs the ``HX-Request`` header that HTMX sends, which a cross-site form can't
set without CORS, so another site can't submit decisions through your browser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from forgeember import envelope
from forgeember.actions import HoldQueue
from forgeember.budget.ledger import Ledger
from forgeember.budget.report import render_text, summarize
from forgeember.config import Paths
from forgeember.web import STATIC_DIR, input_parts, jinja_env


def _receipt_dirs(paths: Paths) -> list[Path]:
    return [paths.receipts_dir, paths.root / "var" / "scale" / "receipts"]


def find_receipt(paths: Paths, item_id: str) -> dict[str, Any] | None:
    if not item_id.startswith("itm_") or not item_id[4:].isalnum():
        return None  # ids are hex; never let a path segment through
    for d in _receipt_dirs(paths):
        p = d / f"{item_id}.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return None


def create_app(paths: Paths) -> FastAPI:
    app = FastAPI(title="ForgeEmber console", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    env = jinja_env()

    def render(name: str, **ctx: Any) -> HTMLResponse:
        return HTMLResponse(env.get_template(name).render(base="/", home="/", htmx=True, **ctx))

    def labels_for(item_ids: set[str]) -> dict[str, str]:
        out = {}
        for item_id in item_ids:
            r = find_receipt(paths, item_id)
            if r:
                subject = (r.get("headers") or {}).get("subject")
                out[item_id] = subject or Path(r.get("source") or item_id).name
        return out

    @app.get("/", response_class=HTMLResponse)
    def holds_page() -> HTMLResponse:
        queue = HoldQueue(paths.holds_db)
        try:
            everything = queue.list(status=None)
            followups: dict[int, list] = {}
            for f in queue.followups():
                followups.setdefault(f.hold_id, []).append(f)
        finally:
            queue.close()
        pending = [h for h in everything if h.status == "pending"]
        decided = [h for h in reversed(everything) if h.status != "pending"][:20]
        ids = {h.item_id for h in everything} | {f.item_id for fs in followups.values() for f in fs}
        labels = labels_for(ids)
        return render(
            "console_holds.html",
            pending=pending,
            decided=decided,
            labels=labels,
            followups=followups,
        )

    @app.post("/holds/{hold_id}/{decision}", response_class=HTMLResponse)
    def decide(
        hold_id: int,
        decision: str,
        decided_by: str = Form(""),
        verification: str = Form(""),
        hx_request: str | None = Header(default=None),
    ) -> HTMLResponse:
        if hx_request != "true":
            raise HTTPException(403, "decisions are accepted from the console only")
        if decision not in ("approve", "reject"):
            raise HTTPException(404)
        if not decided_by.strip():
            raise HTTPException(400, "enter your name before deciding")
        queue = HoldQueue(paths.holds_db)
        try:
            hold = queue.decide(
                hold_id,
                approve=decision == "approve",
                decided_by=decided_by,
                verification=verification,
            )
        except ValueError as exc:
            needs_check = "how you verified" in str(exc)
            raise HTTPException(400 if needs_check else 409, str(exc)) from exc
        finally:
            queue.close()
        row = env.get_template("_hold_row.html").render(h=hold, labels=labels_for({hold.item_id}))
        return HTMLResponse(row)

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_page(item_id: str) -> HTMLResponse:
        r = find_receipt(paths, item_id)
        if r is None:
            raise HTTPException(404, "no receipt for that item")
        source = Path(r.get("source") or "")
        env_ = None
        if source.is_file():
            try:
                env_ = envelope.from_path(source)
            except ValueError:
                env_ = None
        queue = HoldQueue(paths.holds_db)
        try:
            mine = {h.hold_id for h in queue.list(status=None) if h.item_id == item_id}
            later_raw = [f for f in queue.followups() if f.hold_id in mine]
        finally:
            queue.close()
        labels = labels_for({f.item_id for f in later_raw})
        later = [
            {
                "url": f"/items/{f.item_id}",
                "label": labels.get(f.item_id, f.item_id),
                "note": f.note,
                "changes": f.changes,
            }
            for f in later_raw
        ]
        return render("console_item.html", r=r, parts=input_parts(env_), later=later)

    @app.get("/budget", response_class=HTMLResponse)
    def budget_page() -> HTMLResponse:
        with Ledger(db_path=paths.ledger_db, trace_path=paths.calls_trace) as ledger:
            summary = summarize(
                ledger.calls(),
                config=ledger.config,
                effective_limits=ledger.effective_stage_limits(),
                reconciliations=ledger.reconciliations(),
            )
        chart = paths.chart.read_text(encoding="utf-8") if paths.chart.is_file() else None
        return render("console_budget.html", report=render_text(summary), chart=chart)

    return app
