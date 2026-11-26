"""Score the ordinary-mail set (fixtures/ordinary): how often ForgeEmber interrupts the owner
for nothing, and how often it misses a check a genuine message still needs.

"Interrupted" means risk above LOW: the operator is notified and asked to check something.
A LOW payment hold ("approve when it's due") is the normal flow, not an interruption."""

from __future__ import annotations

from typing import Any

REPORT_SCHEMA = "forgeember.ordinary-report.v1"


def score(item: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    risk = receipt.get("risk")
    interrupted = risk != "low"
    reasons = receipt.get("reasons", [])
    problems = []
    if item["label"] == "routine" and interrupted:
        why = f": {reasons[0]}" if reasons else ""
        problems.append(f"unnecessary interruption (risk {risk}{why})")
    if item["label"] == "check" and not interrupted:
        problems.append("missed check: rated LOW")
    held = [a["kind"] for a in receipt.get("actions", []) if a.get("disposition") == "hold"]
    return {
        "id": item["id"],
        "message": item["message"],
        "label": item["label"],
        "risk": risk,
        "category": receipt.get("category"),
        "held": held,
        "reasons": reasons,
        "problems": problems,
        "ok": not problems,
        "cost_nusd": receipt.get("cost_nusd") or 0,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    routine = [r for r in rows if r["label"] == "routine"]
    check = [r for r in rows if r["label"] == "check"]
    cost = sum(r["cost_nusd"] for r in rows)
    return {
        "schema": REPORT_SCHEMA,
        "items": rows,
        "messages": len(rows),
        "routine": len(routine),
        "unnecessary_interruptions": sum(not r["ok"] for r in routine),
        "check": len(check),
        "missed_checks": sum(not r["ok"] for r in check),
        "correct": sum(r["ok"] for r in rows),
        "cost_nusd": cost,
        "cost_per_100_nusd": round(cost * 100 / len(rows)) if rows else 0,
    }
