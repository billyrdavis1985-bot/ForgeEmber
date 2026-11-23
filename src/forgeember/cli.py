"""`forgeember` command line. Phase 0 ships the budget commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from forgeember import __version__
from forgeember.budget.ledger import BudgetError, Ledger, Stage
from forgeember.budget.pricing import fmt_usd, usd_to_nusd
from forgeember.budget.publish import PublishError, publish, snapshot_from_ledger
from forgeember.budget.report import render_text, snapshot, summarize
from forgeember.config import Paths, find_root


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = Paths(root=(args.root or find_root()).resolve())
    try:
        return int(args.func(args, paths))
    except (BudgetError, PublishError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
