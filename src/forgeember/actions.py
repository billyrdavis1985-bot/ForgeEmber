"""Carry out what ``policy.decide`` returned: T0 is recorded, T1 notifies, T2 is held.

Nothing here has an external side effect. "file" and "tag" are recorded on the receipt, a
notification is a line in ``var/notifications.jsonl``, and a hold is a row in the hold queue
that only a named human can approve or reject (the console, Phase 4). No code path executes
a T2 action.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forgeember.budget.ledger import assert_no_secrets
from forgeember.policy import Decision, Tier

_HOLDS_SQL = """
CREATE TABLE IF NOT EXISTS holds (
    hold_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    action TEXT NOT NULL,
    risk TEXT NOT NULL,
    reason TEXT NOT NULL,
    reasons TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by TEXT,
    decided_at TEXT,
    UNIQUE (item_id, action)
)
"""

# What a hold is about, so a later message can be linked to it (cases.py). Added to older
# databases in place; NULL means unknown (no link is ever made on a NULL).
_HOLD_CONTEXT_COLUMNS = {"vendor_id": "TEXT", "invoice_ref": "TEXT", "amount": "TEXT"}

_FOLLOWUPS_SQL = """
CREATE TABLE IF NOT EXISTS followups (
    followup_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hold_id INTEGER NOT NULL REFERENCES holds (hold_id),
    item_id TEXT NOT NULL,
    risk TEXT NOT NULL,
    changes TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (hold_id, item_id)
)
"""
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Hold:
    hold_id: int
    item_id: str
    action: str
    risk: str
    reason: str
    reasons: tuple[str, ...]
    created_at: str
    status: str
    decided_by: str | None
    decided_at: str | None
    vendor_id: str | None = None
    invoice_ref: str | None = None
    amount: str | None = None


@dataclass(frozen=True, slots=True)
class FollowUpRecord:
    followup_id: int
    hold_id: int
    item_id: str
    risk: str
    changes: tuple[str, ...]
    note: str
    created_at: str


class HoldQueue:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_HOLDS_SQL)
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(holds)")}
        for column, sql_type in _HOLD_CONTEXT_COLUMNS.items():
            if column not in have:
                self._conn.execute(f"ALTER TABLE holds ADD COLUMN {column} {sql_type}")
        self._conn.execute(_FOLLOWUPS_SQL)

    def add(
        self,
        *,
        item_id: str,
        action: str,
        risk: str,
        reason: str,
        reasons: tuple[str, ...],
        vendor_id: str | None = None,
        invoice_ref: str | None = None,
        amount: str | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO holds (item_id, action, risk, reason, reasons, created_at, "
                "vendor_id, invoice_ref, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (item_id, action) DO UPDATE SET "
                "risk = excluded.risk, reason = excluded.reason, reasons = excluded.reasons, "
                "vendor_id = excluded.vendor_id, invoice_ref = excluded.invoice_ref, "
                "amount = excluded.amount WHERE holds.status = 'pending' RETURNING hold_id",
                (
                    item_id,
                    action,
                    risk,
                    reason,
                    json.dumps(list(reasons)),
                    _now(),
                    vendor_id,
                    invoice_ref,
                    amount,
                ),
            )
            row = cur.fetchone()
            if row is None:  # already decided by a human: never reopen silently
                row = self._conn.execute(
                    "SELECT hold_id FROM holds WHERE item_id = ? AND action = ?", (item_id, action)
                ).fetchone()
            return int(row[0])

    def list(self, *, status: str | None = "pending") -> list[Hold]:
        sql = "SELECT * FROM holds" + (" WHERE status = ?" if status else "") + " ORDER BY hold_id"
        with self._lock:
            cur = self._conn.execute(sql, (status,) if status else ())
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            d["reasons"] = tuple(json.loads(d["reasons"]))
            out.append(Hold(**d))
        return out

    def decide(self, hold_id: int, *, approve: bool, decided_by: str) -> Hold:
        """Record a human decision. Approval only marks the hold: nothing is paid or sent here."""
        if not decided_by.strip():
            raise ValueError("a hold decision needs a named human")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE holds SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE hold_id = ? AND status = 'pending'",
                ("approved" if approve else "rejected", decided_by.strip(), _now(), hold_id),
            )
            if cur.rowcount != 1:
                raise ValueError(f"hold {hold_id} is not pending")
        return next(h for h in self.list(status=None) if h.hold_id == hold_id)

    def add_followup(
        self, hold_id: int, *, item_id: str, risk: str, changes: tuple[str, ...], note: str
    ) -> int:
        """Attach a later message to a pending hold. The hold's risk can rise, never fall,
        and the hold stays pending: a follow-up never decides anything."""
        with self._lock:
            row = self._conn.execute(
                "SELECT risk, status FROM holds WHERE hold_id = ?", (hold_id,)
            ).fetchone()
            if row is None or row[1] != "pending":
                raise ValueError(f"hold {hold_id} is not pending")
            if _RISK_RANK.get(risk, 2) > _RISK_RANK.get(row[0], 2):
                self._conn.execute("UPDATE holds SET risk = ? WHERE hold_id = ?", (risk, hold_id))
            cur = self._conn.execute(
                "INSERT INTO followups (hold_id, item_id, risk, changes, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (hold_id, item_id) DO UPDATE SET "
                "risk = excluded.risk, changes = excluded.changes, note = excluded.note "
                "RETURNING followup_id",
                (hold_id, item_id, risk, json.dumps(list(changes)), note, _now()),
            )
            return int(cur.fetchone()[0])

    def followups(self, hold_id: int | None = None) -> list[FollowUpRecord]:
        sql = "SELECT * FROM followups" + (" WHERE hold_id = ?" if hold_id else "")
        with self._lock:
            cur = self._conn.execute(sql + " ORDER BY followup_id", (hold_id,) if hold_id else ())
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            d["changes"] = tuple(json.loads(d["changes"]))
            out.append(FollowUpRecord(**d))
        return out

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True, slots=True)
class Outcome:
    executed: tuple[str, ...]
    notified: tuple[str, ...]
    held: tuple[int, ...]  # hold ids


def carry_out(
    decision: Decision,
    *,
    item_id: str,
    holds: HoldQueue,
    notifications: Path,
    vendor_id: str | None = None,
    invoice_ref: str | None = None,
    amount: str | None = None,
) -> Outcome:
    executed, notified, held = [], [], []
    for act in decision.actions:
        if act.tier is Tier.T0:
            executed.append(f"{act.kind}: {act.reason}")
        elif act.tier is Tier.T1:
            line = json.dumps(
                {
                    "ts": _now(),
                    "item_id": item_id,
                    "risk": decision.risk.value,
                    "message": act.reason,
                }
            )
            assert_no_secrets(line)
            notifications.parent.mkdir(parents=True, exist_ok=True)
            with notifications.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            notified.append(act.reason)
        else:
            held.append(
                holds.add(
                    item_id=item_id,
                    action=act.kind,
                    risk=decision.risk.value,
                    reason=act.reason,
                    reasons=decision.reasons,
                    vendor_id=vendor_id,
                    invoice_ref=invoice_ref,
                    amount=amount,
                )
            )
    return Outcome(tuple(executed), tuple(notified), tuple(held))


def write_receipt(receipt: dict[str, Any], directory: Path) -> Path:
    text = json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n"
    assert_no_secrets(text)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{receipt['item_id']}.json"
    path.write_text(text, encoding="utf-8")
    return path


def as_dict(obj: Any) -> Any:
    return asdict(obj)
