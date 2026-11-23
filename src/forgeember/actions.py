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


class HoldQueue:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_HOLDS_SQL)

    def add(
        self, *, item_id: str, action: str, risk: str, reason: str, reasons: tuple[str, ...]
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO holds (item_id, action, risk, reason, reasons, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (item_id, action) DO UPDATE SET "
                "risk = excluded.risk, reason = excluded.reason, reasons = excluded.reasons "
                "WHERE holds.status = 'pending' RETURNING hold_id",
                (item_id, action, risk, reason, json.dumps(list(reasons)), _now()),
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

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True, slots=True)
class Outcome:
    executed: tuple[str, ...]
    notified: tuple[str, ...]
    held: tuple[int, ...]  # hold ids


def carry_out(
    decision: Decision, *, item_id: str, holds: HoldQueue, notifications: Path
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
