"""`forgeember` command line. Phase 0 ships the budget commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from forgeember import __version__, envelope
from forgeember.actions import HoldQueue, carry_out, write_receipt
from forgeember.baseline import BaselineStore
from forgeember.budget.ledger import BudgetError, Ledger, Stage
from forgeember.budget.pricing import fmt_usd, usd_to_nusd
from forgeember.budget.publish import PublishError, publish, snapshot_from_ledger
from forgeember.budget.report import render_text, snapshot, summarize
from forgeember.config import Paths, find_root
from forgeember.ember.client import EmberClient, LiveCallsDisabled
from forgeember.pipeline import Screener, SkepticMode


def _open_ledger(paths: Paths) -> Ledger:
    return Ledger(db_path=paths.ledger_db, trace_path=paths.calls_trace)


def _cmd_report(args: argparse.Namespace, paths: Paths) -> int:
    with _open_ledger(paths) as ledger:
        summary = summarize(
            ledger.calls(),
            config=ledger.config,
            effective_limits=ledger.effective_stage_limits(),
            reconciliations=ledger.reconciliations(),
        )
    if args.json:
        print(json.dumps(snapshot(summary), indent=2, sort_keys=True))
    else:
        print(render_text(summary), end="")
    return 0


def _cmd_reconcile(args: argparse.Namespace, paths: Paths) -> int:
    with _open_ledger(paths) as ledger:
        rec = ledger.reconcile(
            meta_total_nusd=usd_to_nusd(args.meta_usd),
            note=args.note,
            period_start=args.period_start,
            period_end=args.period_end,
            meta_input_tokens=args.meta_input_tokens,
            meta_output_tokens=args.meta_output_tokens,
        )
    print(f"Meta Usage page : {fmt_usd(rec.meta_total_nusd)}")
    print(f"Ledger total    : {fmt_usd(rec.ledger_total_nusd)}")
    print(f"Difference      : {fmt_usd(rec.difference_nusd)} (ledger - Meta)")
    if rec.meta_input_tokens is not None or rec.meta_output_tokens is not None:
        print(
            f"Tokens in/out   : Meta {rec.meta_input_tokens}/{rec.meta_output_tokens} vs ledger "
            f"{rec.ledger_input_tokens}/{rec.ledger_output_tokens}"
        )
    if rec.undercounts:
        print(
            "WARNING: the ledger is BELOW Meta's bill. Stop live calls and investigate "
            "(Muse Code or Playground usage on the same account? missed reasoning tokens?).",
            file=sys.stderr,
        )
        return 2
    print("OK: ledger >= Meta.")
    return 0


def _cmd_transfer(args: argparse.Namespace, paths: Paths) -> int:
    with _open_ledger(paths) as ledger:
        t = ledger.transfer(
            from_stage=Stage(args.from_stage),
            to_stage=Stage(args.to_stage),
            amount_nusd=usd_to_nusd(args.usd),
            approved_by=args.approved_by,
            reason=args.reason,
        )
    print(f"Moved {fmt_usd(t.amount_nusd)} from {t.from_stage} to {t.to_stage} ({t.approved_by}).")
    print("Record this transfer in docs/BUILD_LOG.md.")
    return 0


def _cmd_expire_open(args: argparse.Namespace, paths: Paths) -> int:
    with _open_ledger(paths) as ledger:
        closed = ledger.expire_open(timedelta(minutes=args.older_than_minutes))
    total = sum(c.cost_nusd or 0 for c in closed)
    print(f"Closed {len(closed)} orphaned reservation(s), kept {fmt_usd(total)} as spent.")
    return 0


def _cmd_publish(args: argparse.Namespace, paths: Paths) -> int:
    with _open_ledger(paths) as ledger:
        snap = snapshot_from_ledger(ledger)
    result = publish(paths, snap, check=args.check)
    if args.check and result.changed:
        for p in result.changed:
            print(f"out of date: {p.relative_to(paths.root)}", file=sys.stderr)
        return 1
    for p in result.changed:
        print(f"wrote {p.relative_to(paths.root)}")
    if not result.changed:
        print("budget outputs are up to date")
    return 0


def _screen_and_act(
    screener: Screener, env: envelope.Envelope, holds: HoldQueue, paths: Paths
) -> tuple[Any, Any]:
    """Screen one item, link it to an open hold if exactly one fits, then carry out its own
    decision (so it can never link to itself), and write its receipt."""
    from forgeember.cases import link_followup

    result = screener.screen(env)
    followup = link_followup(result, holds)
    outcome = carry_out(
        result.decision,
        item_id=env.id,
        holds=holds,
        notifications=paths.notifications,
        vendor_id=result.vendor_id,
        invoice_ref=result.invoice_ref,
        amount=result.amount,
    )
    receipt = result.receipt(outcome)
    receipt["followup"] = asdict(followup) if followup else None
    write_receipt(receipt, paths.receipts_dir)
    return result, followup


def _print_screened(env: envelope.Envelope, result: Any, followup: Any) -> None:
    d = result.decision
    held = ", ".join(a.kind for a in d.held) or "-"
    print(
        f"{env.id}  {Path(env.source).name:<32} {result.facts.category:<20} "
        f"risk={d.risk.value:<6} held={held:<28} cost={fmt_usd(result.cost_nusd)}"
    )
    for reason in d.reasons:
        print(f"    - {reason}")
    if followup is not None:
        print(f"    > follow-up on hold #{followup.hold_id} (invoice {followup.invoice_ref}):")
        print(f"      {followup.note}")
    for err in result.errors:
        print(f"    ! {err}")


def _cmd_screen(args: argparse.Namespace, paths: Paths) -> int:
    baselines = BaselineStore.load(args.baselines or paths.baselines)
    envelopes = [envelope.from_path(p) for p in args.paths]  # parse everything before spending
    with _open_ledger(paths) as ledger:
        client = EmberClient.live(ledger, args.stage, live=args.live)
        screener = Screener(
            client=client, baselines=baselines, skeptic_mode=SkepticMode(args.skeptic)
        )
        holds = HoldQueue(paths.holds_db)
        total = 0
        try:
            for env in envelopes:
                result, followup = _screen_and_act(screener, env, holds, paths)
                total += result.cost_nusd
                _print_screened(env, result, followup)
                if result.budget_stopped:
                    print("budget limit reached: stopping the run", file=sys.stderr)
                    return 3
        finally:
            holds.close()
    print(f"screened {len(envelopes)} item(s) for {fmt_usd(total)}; receipts in var/receipts/")
    return 0


def _cmd_holds(args: argparse.Namespace, paths: Paths) -> int:
    queue = HoldQueue(paths.holds_db)
    try:
        items = queue.list(status=None if args.all else "pending")
    finally:
        queue.close()
    if not items:
        print("no holds" if args.all else "no pending holds")
    for h in items:
        print(f"#{h.hold_id:<4} {h.status:<9} {h.risk:<6} {h.action:<20} {h.item_id}  {h.reason}")
        for reason in h.reasons:
            print(f"      - {reason}")
    return 0


def _cmd_holds_decide(args: argparse.Namespace, paths: Paths) -> int:
    queue = HoldQueue(paths.holds_db)
    try:
        hold = queue.decide(args.hold_id, approve=args.approve, decided_by=args.by)
    finally:
        queue.close()
    print(
        f"hold #{hold.hold_id} {hold.status} by {hold.decided_by}. Nothing was paid or sent: "
        "carry out the approved action yourself, outside ForgeEmber."
    )
    return 0


def _cmd_scenario(args: argparse.Namespace, paths: Paths) -> int:
    """Replay a follow-up case against a fresh hold queue and check every expected link."""
    from forgeember.evaluate import load_manifest

    spec = json.loads(args.scenario.read_text(encoding="utf-8"))
    manifest = {i["id"]: i for i in load_manifest(paths.root / "fixtures" / "manifest.json")}
    envs = {
        s["fixture"]: envelope.from_path(paths.root / "fixtures" / manifest[s["fixture"]]["file"])
        for s in spec["steps"]
    }
    db = paths.root / "var" / "scenarios" / f"{spec['name']}.sqlite"
    for stale in db.parent.glob(f"{db.name}*"):
        stale.unlink()
    baselines = BaselineStore.load(paths.baselines)
    steps, total, by_item = [], 0, {e.id: fid for fid, e in envs.items()}
    with _open_ledger(paths) as ledger:
        client = EmberClient.live(ledger, args.stage, live=args.live)
        screener = Screener(client=client, baselines=baselines)
        holds = HoldQueue(db)
        try:
            for step in spec["steps"]:
                env = envs[step["fixture"]]
                result, followup = _screen_and_act(screener, env, holds, paths)
                total += result.cost_nusd
                _print_screened(env, result, followup)
                linked = by_item.get(followup.hold_item_id, "?") if followup else None
                changes = sorted(followup.changes) if followup else []
                ok = linked == step.get("links_to") and changes == sorted(step.get("changes", []))
                steps.append(
                    {
                        "fixture": step["fixture"],
                        "expected_link": step.get("links_to"),
                        "linked_to": linked,
                        "expected_changes": sorted(step.get("changes", [])),
                        "changes": changes,
                        "note": followup.note if followup else None,
                        "hold_risk": followup.risk if followup else None,
                        "ok": ok,
                        "cost_nusd": result.cost_nusd,
                    }
                )
                if result.budget_stopped:
                    print("budget limit reached: stopping the run", file=sys.stderr)
                    return 3
        finally:
            holds.close()
    passed = sum(s["ok"] for s in steps)
    print(f"\n{spec['name']}: {passed}/{len(steps)} steps linked as expected, {fmt_usd(total)}")
    for s in steps:
        mark = "ok" if s["ok"] else "WRONG"
        print(f"  {mark:<6}{s['fixture']:<9} expected {s['expected_link']}, got {s['linked_to']}")
    out_dir = paths.root / "traces" / "scenarios"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"{spec['name']}-{stamp}.json"
    report = {"schema": "forgeember.scenario.v1", "name": spec["name"], "steps": steps}
    out.write_text(json.dumps({**report, "cost_nusd": total}, indent=2) + "\n", encoding="utf-8")
    print(f"report: {out.relative_to(paths.root)}")
    return 0 if passed == len(steps) else 5


def _cmd_eval(args: argparse.Namespace, paths: Paths) -> int:
    from datetime import UTC, datetime

    from forgeember.agents.base import bounded_estimator, counting_estimator
    from forgeember.evaluate import evaluate, load_manifest

    manifest = args.manifest or (paths.root / "fixtures" / "manifest.json")
    items = load_manifest(manifest, args.ids.split(",") if args.ids else None)
    baselines = BaselineStore.load(args.baselines or paths.baselines)
    with _open_ledger(paths) as ledger:
        client = EmberClient.live(ledger, args.stage, live=args.live)
        screener = Screener(
            client=client,
            baselines=baselines,
            skeptic_mode=SkepticMode(args.skeptic),
            estimator=counting_estimator if args.count_tokens else bounded_estimator,
        )
        report = evaluate(
            items,
            fixtures_dir=manifest.parent,
            screen=lambda _i, env: screener.screen(env),
            ledger=ledger,
            skeptic_mode=args.skeptic,
            stage=args.stage,
        )
    print(report.render_text(), end="")
    out_dir = paths.root / "traces" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = out_dir / f"eval-{stamp}-{args.skeptic}.json"
    out.write_text(json.dumps(report.to_dict(), indent=2, default=str) + "\n", encoding="utf-8")
    print(f"report: {out.relative_to(paths.root)}")
    return 0 if report.gate_a else 4


def _cmd_batch(args: argparse.Namespace, paths: Paths) -> int:
    from forgeember.batch.runner import run_batch, write_summary
    from forgeember.corpus import interleave, load_corpus

    groups = [load_corpus([p]) for p in args.paths]
    items = interleave(*groups)
    if not items:
        print("error: no items found", file=sys.stderr)
        return 1
    baselines = BaselineStore.load(args.baselines or paths.baselines)
    with _open_ledger(paths) as ledger:
        client = EmberClient.live(ledger, args.stage, live=args.live)
        screener = Screener(
            client=client,
            baselines=baselines,
            skeptic_mode=SkepticMode(args.skeptic),
            bulk=not args.full,
        )
        stats = run_batch(
            items,
            screener.screen,
            receipts_dir=args.receipts_dir or paths.root / "var" / "scale" / "receipts",
            workers=args.workers,
            limit=args.limit,
            resume=not args.no_resume,
            progress=print,
        )
    print(stats.line())
    if stats.skipped_resume:
        print(f"skipped {stats.skipped_resume} item(s) already screened (resume)")
    out = write_summary(
        stats,
        paths.root / "traces" / "scale",
        {
            "stage": args.stage,
            "limit": args.limit,
            "workers": args.workers,
            "skeptic": args.skeptic,
            "bulk": not args.full,
            "sources": [str(p) for p in args.paths],
        },
    )
    print(f"summary: {out.relative_to(paths.root)}")
    if stats.budget_stopped:
        print("stage budget reached: run stopped cleanly", file=sys.stderr)
        return 3
    return 0


def _cmd_console(args: argparse.Namespace, paths: Paths) -> int:
    import uvicorn

    from forgeember.console.app import create_app

    print(f"ForgeEmber console on http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(create_app(paths), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _cmd_demo_export(args: argparse.Namespace, paths: Paths) -> int:
    from forgeember.site import export_demo

    run = export_demo(paths)
    print(
        f"exported {len(run['items'])} fixture receipt(s) to traces/demo/ "
        f"({fmt_usd(run['cost_nusd'])}); prompts: {', '.join(run['prompt_versions']) or '-'}"
    )
    if run["missing"]:
        print(f"not yet screened: {', '.join(run['missing'])}")
    return 0


def _cmd_site_build(args: argparse.Namespace, paths: Paths) -> int:
    from forgeember.site import build_site

    out = args.out or (paths.root / "site")
    written = build_site(paths, out)
    print(f"wrote {len(written)} pages to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgeember", description=__doc__)
    parser.add_argument("--version", action="version", version=f"forgeember {__version__}")
    parser.add_argument("--root", type=Path, default=None, help="repository root (auto-detected)")
    sub = parser.add_subparsers(dest="command", required=True)

    budget = sub.add_parser("budget", help="budget report and ledger operations")
    budget.add_argument("--json", action="store_true", help="print the snapshot as JSON")
    budget.set_defaults(func=_cmd_report)
    bsub = budget.add_subparsers(dest="budget_command")

    rec = bsub.add_parser("reconcile", help="record Meta's Usage-page total next to the ledger")
    rec.add_argument("--meta-usd", required=True, help="total from Meta's Usage/Billing page")
    rec.add_argument("--meta-input-tokens", type=int)
    rec.add_argument("--meta-output-tokens", type=int)
    rec.add_argument("--period-start")
    rec.add_argument("--period-end")
    rec.add_argument("--note", default="")
    rec.set_defaults(func=_cmd_reconcile)

    tr = bsub.add_parser("transfer", help="move headroom between stages (needs Billy's approval)")
    tr.add_argument("--from-stage", required=True, choices=[s.value for s in Stage])
    tr.add_argument("--to-stage", required=True, choices=[s.value for s in Stage])
    tr.add_argument("--usd", required=True)
    tr.add_argument("--approved-by", required=True)
    tr.add_argument("--reason", required=True)
    tr.set_defaults(func=_cmd_transfer)

    ex = bsub.add_parser("expire-open", help="close reservations orphaned by a crash")
    ex.add_argument("--older-than-minutes", type=int, default=60)
    ex.set_defaults(func=_cmd_expire_open)

    pub = bsub.add_parser("publish", help="write snapshot, chart and README budget block")
    pub.add_argument("--check", action="store_true", help="fail if outputs are out of date")
    pub.set_defaults(func=_cmd_publish)

    sc = sub.add_parser("screen", help="screen items with Muse Ember (live; costs money)")
    sc.add_argument("paths", nargs="+", type=Path, help=".eml, PDF, image or audio files")
    sc.add_argument("--stage", required=True, choices=[s.value for s in Stage])
    sc.add_argument("--live", action="store_true", help="required: make real API calls")
    sc.add_argument("--baselines", type=Path, help="vendor baselines JSON (default: fixtures)")
    sc.add_argument("--skeptic", default="escalated", choices=[m.value for m in SkepticMode])
    sc.set_defaults(func=_cmd_screen)

    scn = sub.add_parser("scenario", help="replay a follow-up case and check its links (live)")
    scn.add_argument("scenario", type=Path, help="e.g. fixtures/scenarios/invoice-1047.json")
    scn.add_argument("--stage", required=True, choices=[s.value for s in Stage])
    scn.add_argument("--live", action="store_true", help="required: make real API calls")
    scn.set_defaults(func=_cmd_scenario)

    ev = sub.add_parser("eval", help="score the labeled fixtures (Gate A; live, costs money)")
    ev.add_argument("--stage", required=True, choices=[s.value for s in Stage])
    ev.add_argument("--live", action="store_true", help="required: make real API calls")
    ev.add_argument("--ids", help="comma-separated fixture ids (default: all)")
    ev.add_argument("--manifest", type=Path, help="default: fixtures/manifest.json")
    ev.add_argument("--baselines", type=Path, help="default: fixtures/baselines.json")
    ev.add_argument("--skeptic", default="escalated", choices=[m.value for m in SkepticMode])
    ev.add_argument(
        "--count-tokens",
        action="store_true",
        help="bound media with the token-count endpoint instead of static bounds",
    )
    ev.set_defaults(func=_cmd_eval)

    ba = sub.add_parser("batch", help="screen a labeled corpus at scale (live, costs money)")
    ba.add_argument(
        "paths", nargs="+", type=Path, help="corpus folders (with labels.jsonl) or files"
    )
    ba.add_argument("--stage", required=True, choices=[s.value for s in Stage])
    ba.add_argument("--live", action="store_true", help="required: make real API calls")
    ba.add_argument("--limit", type=int, help="screen at most N items (sampled across sources)")
    ba.add_argument("--workers", type=int, default=4)
    ba.add_argument("--skeptic", default="off", choices=[m.value for m in SkepticMode])
    ba.add_argument("--full", action="store_true", help="strict call plan instead of bulk mode")
    ba.add_argument("--no-resume", action="store_true", help="re-screen items that have receipts")
    ba.add_argument(
        "--receipts-dir",
        type=Path,
        help="default: var/scale/receipts (use another for a check run)",
    )
    ba.add_argument("--baselines", type=Path, help="default: fixtures/baselines.json")
    ba.set_defaults(func=_cmd_batch)

    co = sub.add_parser("console", help="local approval console (127.0.0.1)")
    co.add_argument("--port", type=int, default=8000)
    co.set_defaults(func=_cmd_console)

    de = sub.add_parser("demo", help="recorded demo runs")
    dsub = de.add_subparsers(dest="demo_command", required=True)
    dex = dsub.add_parser("export", help="copy fixture receipts into traces/demo/ (no API calls)")
    dex.set_defaults(func=_cmd_demo_export)

    si = sub.add_parser("site", help="static keyless replay site")
    ssub = si.add_subparsers(dest="site_command", required=True)
    sbu = ssub.add_parser("build", help="build site/ from committed traces and fixtures")
    sbu.add_argument("--out", type=Path, help="default: site/")
    sbu.set_defaults(func=_cmd_site_build)

    ho = sub.add_parser("holds", help="list held (T2) actions")
    ho.add_argument("--all", action="store_true", help="include decided holds")
    ho.set_defaults(func=_cmd_holds)
    hsub = ho.add_subparsers(dest="holds_command")
    hd = hsub.add_parser("decide", help="record a human decision on a hold (Billy only)")
    hd.add_argument("hold_id", type=int)
    group = hd.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    hd.add_argument("--by", required=True, help="the human making the decision")
    hd.set_defaults(func=_cmd_holds_decide)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = Paths(root=(args.root or find_root()).resolve())
    try:
        return int(args.func(args, paths))
    except (BudgetError, PublishError, LiveCallsDisabled, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
