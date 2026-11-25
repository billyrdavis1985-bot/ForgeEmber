"""Resumable, budget-capped scale runs over labeled corpora.

* Every call goes through the ledger, so a run stops cleanly at the stage limit
  (``budget_stopped``) instead of overspending. No new items start after that.
* Resume: an item with a receipt in ``receipts_dir`` is skipped. A crashed or stopped run
  continues where it left off and never pays twice for the same item.
* Items that errored (unparseable file, API error) get no receipt, so a resumed run retries
  them. They're reported separately and excluded from precision and recall.
* Detection metric: an item is "flagged" when the decision is MEDIUM or HIGH risk.
  Positive label = "phish". Every other label (benign mail, receipts) is a negative.
* Known answers: when a corpus line carries ``expected`` (CORD receipts do), the final route
  and the extracted total are scored against it (``accuracy`` in the summary).
* No actions are carried out and no holds are created: a scale run measures, it doesn't act.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from forgeember import envelope
from forgeember.actions import write_receipt
from forgeember.budget.pricing import fmt_usd
from forgeember.corpus import CorpusItem
from forgeember.pipeline import ScreenResult

POSITIVE = "phish"


def _amount_digits(value: str) -> str:
    """Digits of an amount, ignoring separators, currency marks and a zero decimal part."""
    return re.sub(r"\D", "", re.sub(r"[.,]0{1,2}$", "", value.strip()))


def amounts_match(found: str | None, expected: str) -> bool:
    """Same printed amount, judged on its digits.

    Separators vary by locale ("60.000" and "60,000" are both sixty thousand rupiah in CORD),
    and the model is told to drop them, so "60000" matches. A trailing ".00" is ignored, so
    "60000.00" matches too, but a digit that is wrong, missing or extra never does.
    """
    if not found:
        return False
    a, b = _amount_digits(found), _amount_digits(expected)
    return bool(a) and a == b


@dataclass
class BatchStats:
    total: int = 0
    processed: int = 0
    skipped_resume: int = 0
    retried: int = 0
    incomplete: int = 0  # screened with a permanent agent error: >= MEDIUM, kept
    errors: int = 0
    budget_stopped: bool = False
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    unlabeled: int = 0
    cost_nusd: int = 0
    calls: int = 0
    elapsed_s: float = 0.0
    by_category: dict[str, int] = field(default_factory=dict)
    by_label: dict[str, int] = field(default_factory=dict)
    scored: dict[str, list[int]] = field(default_factory=dict)  # name -> [correct, labeled]
    error_samples: list[str] = field(default_factory=list)

    def score(self, name: str, correct: bool) -> None:
        tally = self.scored.setdefault(name, [0, 0])
        tally[0] += correct
        tally[1] += 1

    def accuracy(self) -> dict[str, dict[str, Any]]:
        return {
            name: {"correct": ok, "labeled": n, "rate": str(self._ratio(ok, n))}
            for name, (ok, n) in sorted(self.scored.items())
        }

    @staticmethod
    def _ratio(num: int, den: int) -> Decimal | None:
        return None if den == 0 else (Decimal(num) / den).quantize(Decimal("0.001"))

    @property
    def precision(self) -> Decimal | None:
        return self._ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> Decimal | None:
        return self._ratio(self.tp, self.tp + self.fn)

    @property
    def false_positive_rate(self) -> Decimal | None:
        return self._ratio(self.fp, self.fp + self.tn)

    @property
    def cost_per_item_nusd(self) -> int | None:
        return None if self.processed == 0 else self.cost_nusd // self.processed

    def line(self) -> str:
        def pct(x: Decimal | None) -> str:
            return "n/a" if x is None else f"{x * 100:.1f}%"

        per = self.cost_per_item_nusd
        scores = "".join(
            f" · {name} {pct(self._ratio(ok, n))}" for name, (ok, n) in sorted(self.scored.items())
        )
        return (
            f"{self.processed}/{self.total} screened · {self.errors} errors · "
            f"cost {fmt_usd(self.cost_nusd)} ({fmt_usd(per) if per is not None else 'n/a'}/item) · "
            f"precision {pct(self.precision)} · recall {pct(self.recall)} · "
            f"FPR {pct(self.false_positive_rate)}{scores}"
        )

    def to_dict(self) -> dict[str, Any]:
        per = self.cost_per_item_nusd
        return {
            "schema": "forgeember.scale.v1",
            "total": self.total,
            "processed": self.processed,
            "skipped_resume": self.skipped_resume,
            "retried": self.retried,
            "incomplete": self.incomplete,
            "errors": self.errors,
            "budget_stopped": self.budget_stopped,
            "confusion": {"tp": self.tp, "fp": self.fp, "tn": self.tn, "fn": self.fn},
            "unlabeled": self.unlabeled,
            "precision": None if self.precision is None else str(self.precision),
            "recall": None if self.recall is None else str(self.recall),
            "false_positive_rate": None
            if self.false_positive_rate is None
            else str(self.false_positive_rate),
            "cost_nusd": self.cost_nusd,
            "calls": self.calls,
            "cost_per_item_nusd": per,
            "projected_10k_items_nusd": None if per is None else per * 10_000,
            "elapsed_s": round(self.elapsed_s, 1),
            "by_category": dict(sorted(self.by_category.items())),
            "by_label": dict(sorted(self.by_label.items())),
            "accuracy": self.accuracy(),
            "error_samples": self.error_samples[:20],
        }


def run_batch(
    items: list[CorpusItem],
    screen: Callable[[envelope.Envelope], ScreenResult],
    *,
    receipts_dir: Path,
    workers: int = 1,
    limit: int | None = None,
    resume: bool = True,
    progress: Callable[[str], None] | None = None,
    progress_every: int = 50,
    retries: int = 2,
    retry_delay_s: float = 60.0,  # Meta's Retry-After for 503 backend_unavailable
    sleep: Callable[[float], None] = time.sleep,
) -> BatchStats:
    """Screen items; transient screening errors get ``retries`` extra passes.

    Unparseable files are permanent errors and never retried. Items that failed screening
    (API errors, invalid output) are retried after ``retry_delay_s``. Meta's 503s come with
    Retry-After 60, so the default pass-level delay plus the other items usually covers it.
    """
    stats = BatchStats(total=len(items) if limit is None else min(limit, len(items)))
    lock = threading.Lock()
    stop = threading.Event()
    started = time.monotonic()
    retry: list[CorpusItem] = []
    last_errors: dict[str, str] = {}

    def one(item: CorpusItem) -> None:
        if stop.is_set():
            return
        try:
            env = envelope.from_path(item.path)
        except Exception as exc:  # one bad file never aborts the run; never retried
            with lock:
                stats.errors += 1
                stats.error_samples.append(f"{item.path.name}: unparseable ({type(exc).__name__})")
            return
        if resume and (receipts_dir / f"{env.id}.json").exists():
            with lock:
                stats.skipped_resume += 1
            return
        result = screen(env)
        with lock:
            stats.cost_nusd += result.cost_nusd
            stats.calls += len(result.calls)
            if result.budget_stopped:
                stats.budget_stopped = True
                stop.set()
            if result.budget_stopped or result.decision is None or result.retryable:
                retry.append(item)  # no receipt: retried in the next pass, or on resume
                last_errors[item.path.name] = "; ".join(result.errors)[:160]
                return
            stats.processed += 1
            if result.errors:  # permanent (content policy, bad request): decided conservatively
                stats.incomplete += 1
                stats.error_samples.append(
                    f"{item.path.name}: kept as incomplete: {'; '.join(result.errors)[:120]}"
                )
            cat = result.facts.category if result.facts else "unknown"
            stats.by_category[cat] = stats.by_category.get(cat, 0) + 1
            flagged = result.decision.risk.value in ("medium", "high")
            if item.label is None:
                stats.unlabeled += 1
            else:
                stats.by_label[item.label] = stats.by_label.get(item.label, 0) + 1
                if item.label == POSITIVE:
                    stats.tp += flagged
                    stats.fn += not flagged
                else:
                    stats.fp += flagged
                    stats.tn += not flagged
            expected = item.expected or {}
            matched: dict[str, bool] = {}
            if "category" in expected:
                matched["category"] = cat == expected["category"]
            if "total" in expected:
                found = result.extraction.total if result.extraction else None
                matched["total"] = amounts_match(found, expected["total"])
            for name, ok in matched.items():
                stats.score(name, ok)
            receipt = result.receipt()
            receipt["corpus"] = {
                "label": item.label,
                "source": item.source,
                "file": item.path.name,
                "expected": expected or None,
                "matched": matched or None,
            }
            write_receipt(receipt, receipts_dir)
            if progress and stats.processed % progress_every == 0:
                progress(stats.line())

    def run_pass(queue: list[CorpusItem]) -> None:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            pending: set[Future] = set()
            for item in queue:
                if stop.is_set():
                    break
                pending.add(pool.submit(one, item))
                if len(pending) >= max(1, workers):
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for f in done:
                        f.result()
            for f in pending:
                f.result()

    queue = list(items[: stats.total])
    for attempt in range(retries + 1):
        run_pass(queue)
        if not retry or stop.is_set() or attempt == retries:
            break
        if progress:
            progress(f"retrying {len(retry)} item(s) that failed screening in {retry_delay_s:.0f}s")
        sleep(retry_delay_s)
        queue, retry[:] = list(retry), []
        stats.retried += len(queue)
    stats.errors += len(retry)
    stats.error_samples += [f"{i.path.name}: {last_errors.get(i.path.name, '')}" for i in retry]
    stats.elapsed_s = time.monotonic() - started
    return stats


def write_summary(stats: BatchStats, out_dir: Path, config: dict[str, Any]) -> Path:
    from datetime import UTC, datetime

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"scale-{stamp}.json"
    path.write_text(
        json.dumps({**stats.to_dict(), "config": config}, indent=2) + "\n", encoding="utf-8"
    )
    return path
