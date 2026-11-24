"""Gate A evaluation: screen the labeled fixtures and score every proposal against its label.

Scores:
* routing: Ember's category (after code's corrections) equals the label
* risk: the final risk level equals the label
* holds: the set of held (T2) actions equals the label
* fields: extracted invoice fields equal the label (vendor, number, total, currency, IBAN),
  and for voicemails what was heard (caller's company, callback number, account read out, amount)

Cost comes from the ledger (every call tagged with the item, token counts included). The run
never carries out actions and never creates holds. Gate A passes at >= 90% routing and fields.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from forgeember import envelope
from forgeember.baseline import _norm_name, normalize_iban, parse_amount
from forgeember.budget.ledger import Ledger
from forgeember.budget.pricing import fmt_usd
from forgeember.pipeline import ScreenResult

GATE_A_THRESHOLD = Decimal("0.90")
FIELDS = ("vendor_name", "invoice_number", "total", "currency", "iban")
VOICEMAIL_FIELDS = (
    "caller_organization",
    "callback_number",
    "account_number",
    "routing_number",
    "amount",
)
_DIGIT_FIELDS = ("callback_number", "account_number", "routing_number")

ScreenFn = Callable[[dict[str, Any], envelope.Envelope], ScreenResult]


def _field_equal(name: str, expected: str, got: str | None) -> bool:
    if got is None:
        return False
    if name in ("vendor_name", "caller_organization"):
        return _norm_name(expected) == _norm_name(got)
    if name in ("total", "amount"):
        return parse_amount(expected) == parse_amount(got)
    if name in _DIGIT_FIELDS:
        return re.sub(r"\D", "", expected) == re.sub(r"\D", "", got)
    if name == "iban":
        return normalize_iban(expected) == normalize_iban(got)
    return expected.strip().upper() == got.strip().upper()


def _got_fields(result: ScreenResult) -> dict[str, str | None]:
    got: dict[str, str | None] = dict.fromkeys(FIELDS + VOICEMAIL_FIELDS)
    if (ext := result.extraction) is not None:
        got.update(
            vendor_name=ext.vendor_name,
            invoice_number=ext.invoice_number,
            total=ext.total,
            currency=ext.currency,
            iban=ext.bank.iban if ext.bank else None,
        )
    if (vm := result.voicemail) is not None:
        got.update(
            caller_organization=vm.caller_organization,
            callback_number=vm.callback_number,
            account_number=vm.bank.account_number if vm.bank else None,
            routing_number=vm.bank.routing_number if vm.bank else None,
            amount=vm.amount,
        )
    return got


@dataclass
class ItemScore:
    id: str
    kind: str
    expected_category: str
    category: str | None
    expected_risk: str
    risk: str | None
    expected_held: list[str]
    held: list[str]
    fields: dict[str, bool]
    calls: int
    cost_nusd: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    errors: list[str] = field(default_factory=list)

    @property
    def routing_ok(self) -> bool:
        return self.category == self.expected_category

    @property
    def risk_ok(self) -> bool:
        return self.risk == self.expected_risk

    @property
    def held_ok(self) -> bool:
        return sorted(self.held) == sorted(self.expected_held)


def _rate(hits: int, total: int) -> Decimal | None:
    return None if total == 0 else (Decimal(hits) / total).quantize(Decimal("0.001"))


@dataclass
class EvalReport:
    items: list[ItemScore]
    skeptic_mode: str
    stage: str

    def _share(self, attr: str) -> Decimal | None:
        return _rate(sum(1 for i in self.items if getattr(i, attr)), len(self.items))

    @property
    def routing_accuracy(self) -> Decimal | None:
        return self._share("routing_ok")

    @property
    def risk_accuracy(self) -> Decimal | None:
        return self._share("risk_ok")

    @property
    def held_accuracy(self) -> Decimal | None:
        return self._share("held_ok")

    @property
    def field_accuracy(self) -> Decimal | None:
        checks = [ok for i in self.items for ok in i.fields.values()]
        return _rate(sum(checks), len(checks))

    @property
    def gate_a(self) -> bool:
        routing, fields_ = self.routing_accuracy, self.field_accuracy
        return (
            routing is not None
            and routing >= GATE_A_THRESHOLD
            and (fields_ is None or fields_ >= GATE_A_THRESHOLD)
        )

    @property
    def cost_nusd(self) -> int:
        return sum(i.cost_nusd for i in self.items)

    def cost_by_kind(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for i in self.items:
            row = out.setdefault(i.kind, {"items": 0, "cost_nusd": 0, "calls": 0})
            row["items"] += 1
            row["cost_nusd"] += i.cost_nusd
            row["calls"] += i.calls
        return out

    def to_dict(self) -> dict[str, Any]:
        def s(x: Decimal | None) -> str | None:
            return None if x is None else str(x)

        tokens = {
            k: sum(getattr(i, k) for i in self.items)
            for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")
        }
        return {
            "schema": "forgeember.eval.v1",
            "stage": self.stage,
            "skeptic_mode": self.skeptic_mode,
            "items_scored": len(self.items),
            "routing_accuracy": s(self.routing_accuracy),
            "risk_accuracy": s(self.risk_accuracy),
            "held_accuracy": s(self.held_accuracy),
            "field_accuracy": s(self.field_accuracy),
            "gate_a_pass": self.gate_a,
            "cost_nusd": self.cost_nusd,
            "cost_by_kind": self.cost_by_kind(),
            "tokens": tokens,
            "items": [
                {
                    **asdict(i),
                    "routing_ok": i.routing_ok,
                    "risk_ok": i.risk_ok,
                    "held_ok": i.held_ok,
                }
                for i in self.items
            ],
        }

    def render_text(self) -> str:
        def pct(x: Decimal | None) -> str:
            return "n/a" if x is None else f"{x * 100:.1f}%"

        lines = [
            f"{'item':<9}{'kind':<7}{'category':<22}{'risk':<16}{'held':<6}{'fields':<8}"
            f"{'calls':>5} {'cost':>10}"
        ]
        for i in self.items:
            cat = i.category if i.routing_ok else f"{i.category}!={i.expected_category}"
            risk = i.risk if i.risk_ok else f"{i.risk}!={i.expected_risk}"
            fields_ = f"{sum(i.fields.values())}/{len(i.fields)}" if i.fields else "-"
            lines.append(
                f"{i.id:<9}{i.kind:<7}{(cat or '-'):<22}{(risk or '-'):<16}"
                f"{'ok' if i.held_ok else 'NO':<6}{fields_:<8}{i.calls:>5} "
                f"{fmt_usd(i.cost_nusd):>10}"
            )
            lines += [f"    ! {e}" for e in i.errors]
        lines += [
            "",
            f"routing {pct(self.routing_accuracy)} · fields {pct(self.field_accuracy)} · "
            f"risk {pct(self.risk_accuracy)} · holds {pct(self.held_accuracy)}",
            f"Gate A (>= 90% routing and fields): {'PASS' if self.gate_a else 'FAIL'}",
            f"cost {fmt_usd(self.cost_nusd)} for {len(self.items)} items",
        ]
        for kind, row in self.cost_by_kind().items():
            per = row["cost_nusd"] // max(row["items"], 1)
            lines.append(
                f"  {kind:<6} {row['items']:>3} items  {row['calls']:>3} calls  "
                f"{fmt_usd(per)}/item  -> {fmt_usd(per * 10_000)} per 10,000"
            )
        return "\n".join(lines) + "\n"


def load_manifest(path: Path, ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))["items"]
    if ids:
        wanted = set(ids)
        items = [i for i in items if i["id"] in wanted]
        missing = wanted - {i["id"] for i in items}
        if missing:
            raise ValueError(f"unknown fixture ids: {sorted(missing)}")
    return items


def evaluate(
    items: list[dict[str, Any]],
    *,
    fixtures_dir: Path,
    screen: ScreenFn,
    ledger: Ledger,
    skeptic_mode: str,
    stage: str,
) -> EvalReport:
    scores: list[ItemScore] = []
    for item in items:
        env = envelope.from_path(fixtures_dir / item["file"])
        before = {c.call_id for c in ledger.calls()}
        result = screen(item, env)
        calls = [c for c in ledger.calls() if c.call_id not in before and c.item_id == env.id]
        exp = item["expected"]
        got = _got_fields(result)
        fields_ = {k: _field_equal(k, v, got.get(k)) for k, v in exp.get("fields", {}).items()}
        scores.append(
            ItemScore(
                id=item["id"],
                kind=env.kind.value,
                expected_category=exp["category"],
                category=result.facts.category if result.facts else None,
                expected_risk=exp["risk"],
                risk=result.decision.risk.value if result.decision else None,
                expected_held=list(exp["held"]),
                held=[a.kind for a in result.decision.held] if result.decision else [],
                fields=fields_,
                calls=len(calls),
                cost_nusd=sum(c.cost_nusd or 0 for c in calls),
                input_tokens=sum(c.input_tokens or 0 for c in calls),
                cached_input_tokens=sum(c.cached_input_tokens or 0 for c in calls),
                output_tokens=sum(c.output_tokens or 0 for c in calls),
                reasoning_tokens=sum(c.reasoning_tokens or 0 for c in calls),
                errors=list(result.errors),
            )
        )
        if result.budget_stopped:
            break
    return EvalReport(items=scores, skeptic_mode=skeptic_mode, stage=stage)
