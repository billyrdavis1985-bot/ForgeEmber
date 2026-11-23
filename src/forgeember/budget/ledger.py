"""Budget ledger: reserve before a call, settle after it, refuse past any limit.

Authority and invariants (see docs/BUDGET.md):

* SQLite (WAL) is the source of truth. The limit check and the reservation insert run in one
  ``BEGIN IMMEDIATE`` transaction, so they are atomic across threads *and* processes.
* committed(stage) = sum(settled cost in stage) + sum(open reservations in stage).
  A reservation is refused if committed(stage) + request > effective stage limit, or
  committed(all) + request > global cap.
* A call settles at its reported usage. If usage is missing, or the call failed, it settles
  at max(full reservation, any reported usage). Cost is never capped at the reservation:
  an overrun is recorded at the actual amount and flagged.
* Each settled call is mirrored as one line in an append-only JSONL trace. The trace never
  contains secrets: a line that looks like an API key or auth header is refused.
* Moving money between stages needs a named approver and a reason, and is logged.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from forgeember.budget.pricing import (
    actual_cost,
    nusd_to_usd,
    pricing_for,
    usd_to_nusd,
    worst_case_cost,
)

try:  # POSIX advisory lock for cross-process JSONL appends (Linux / WSL).
    import fcntl
except ImportError:  # pragma: no cover - Windows without WSL
    fcntl = None  # type: ignore[assignment]

SCHEMA_VERSION = 1
TRACE_SCHEMA = "forgeember.trace.v1"
EVENT_SCHEMA = "forgeember.budget-event.v1"


class Stage(StrEnum):
    DEV = "dev"
    EVAL = "eval"
    SCALE = "scale"
    DEMO = "demo"
    RESERVE = "reserve"


class InputKind(StrEnum):
    EMAIL = "email"
    PDF = "pdf"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"  # plain text calls with no source document (e.g. the smoke test)


class Outcome(StrEnum):
    OK = "ok"  # completed, usage reported
    INCOMPLETE = "incomplete"  # stopped early (e.g. max_output_tokens), usage reported
    FAILED = "failed"  # transport error, timeout, 5xx: billing unknown
    REJECTED = "rejected"  # API returned a 4xx error body
    NO_USAGE = "no_usage"  # 2xx but usage missing or malformed
    ORPHANED = "orphaned"  # reservation never settled (crash); closed by expire_open


USAGE_TRUSTED_OUTCOMES = frozenset({Outcome.OK, Outcome.INCOMPLETE})

ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/@+=-]{1,200}$")

# Anything shaped like a credential must never reach the trace. Meta keys start with LLM_
# followed by a numeric id and the secret (https://dev.meta.ai/docs/authentication).
SECRET_PATTERNS = (
    re.compile(r"LLM_\d{6,}_\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)authorization\s*[:=]"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{16,}"),
)


class BudgetError(Exception):
    """Base class for budget failures."""


class BudgetExceeded(BudgetError):
    """A reservation would push committed spend past a limit. The call must not be made."""

    def __init__(
        self, *, scope: str, limit_nusd: int, committed_nusd: int, requested_nusd: int
    ) -> None:
        self.scope = scope
        self.limit_nusd = limit_nusd
        self.committed_nusd = committed_nusd
        self.requested_nusd = requested_nusd
        super().__init__(
            f"budget refused ({scope}): committed {nusd_to_usd(committed_nusd)} + requested "
            f"{nusd_to_usd(requested_nusd)} USD > limit {nusd_to_usd(limit_nusd)} USD"
        )


class StageLimitExceeded(BudgetExceeded):
    pass


class GlobalCapExceeded(BudgetExceeded):
    pass


class SecretInTraceError(BudgetError):
    """A trace line matched a credential pattern. Nothing was written."""


class LedgerStateError(BudgetError):
    """Invalid state transition, e.g. settling a call twice."""


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    grant_nusd: int
    global_cap_nusd: int
    stage_limits_nusd: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.global_cap_nusd <= 0 or self.global_cap_nusd > self.grant_nusd:
            raise ValueError("global cap must be > 0 and <= the grant")
        unknown = set(self.stage_limits_nusd) - {s.value for s in Stage}
        if unknown:
            raise ValueError(f"unknown stages: {sorted(unknown)}")
        if any(v < 0 for v in self.stage_limits_nusd.values()):
            raise ValueError("stage limits must be >= 0")
        if sum(self.stage_limits_nusd.values()) > self.global_cap_nusd:
            raise ValueError("stage limits must not sum above the global cap")

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_nusd": self.grant_nusd,
            "global_cap_nusd": self.global_cap_nusd,
            "stage_limits_nusd": dict(self.stage_limits_nusd),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BudgetConfig:
        return cls(
            grant_nusd=int(data["grant_nusd"]),
            global_cap_nusd=int(data["global_cap_nusd"]),
            stage_limits_nusd={k: int(v) for k, v in data["stage_limits_nusd"].items()},
        )


DEFAULT_CONFIG = BudgetConfig(
    grant_nusd=usd_to_nusd("10.00"),
    global_cap_nusd=usd_to_nusd("9.00"),
    stage_limits_nusd={
        Stage.DEV.value: usd_to_nusd("1.50"),
        Stage.EVAL.value: usd_to_nusd("1.50"),
        Stage.SCALE.value: usd_to_nusd("4.00"),
        Stage.DEMO.value: usd_to_nusd("0.75"),
        Stage.RESERVE.value: usd_to_nusd("1.25"),
    },
)


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage as reported by the API (Responses API field names)."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    web_search_calls: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"usage.{name} must be a non-negative int, got {value!r}")


@dataclass(frozen=True, slots=True)
class CallRequest:
    """Everything the ledger needs to know about a call before it is made."""

    stage: Stage
    model: str
    agent_role: str
    input_kind: InputKind
    reasoning_effort: str
    est_input_tokens: int  # an UPPER BOUND on billable input tokens
    max_output_tokens: int
    item_id: str | None = None
    prompt_version: str | None = None
    prompt_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", Stage(self.stage))
        object.__setattr__(self, "input_kind", InputKind(self.input_kind))
        pricing_for(self.model)  # unknown model -> refuse before anything else
        if not ROLE_PATTERN.match(self.agent_role):
            raise ValueError(f"agent_role {self.agent_role!r} must match {ROLE_PATTERN.pattern}")
        if not ROLE_PATTERN.match(self.reasoning_effort):
            raise ValueError("reasoning_effort must be a plain lowercase token")
        if self.est_input_tokens < 0 or self.max_output_tokens < 1:
            raise ValueError("est_input_tokens must be >= 0 and max_output_tokens >= 1")
        for name in ("item_id", "prompt_version"):
            value = getattr(self, name)
            if value is not None and not SAFE_ID_PATTERN.match(value):
                raise ValueError(f"{name} {value!r} must match {SAFE_ID_PATTERN.pattern}")
        if self.prompt_sha256 is not None and not SHA256_PATTERN.match(self.prompt_sha256):
            raise ValueError("prompt_sha256 must be 64 lowercase hex characters")


@dataclass(frozen=True, slots=True)
class Reservation:
    call_id: str
    request: CallRequest
    reservation_nusd: int
    reserved_at: datetime


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One row of the ledger. ``to_trace`` gives the JSONL line for a settled call."""

    call_id: str
    status: str  # "open" | "settled"
    reserved_at: str
    settled_at: str | None
    stage: str
    agent_role: str
    model: str
    reasoning_effort: str
    input_kind: str
    item_id: str | None
    prompt_version: str | None
    prompt_sha256: str | None
    est_input_tokens: int
    max_output_tokens: int
    reservation_nusd: int
    outcome: str | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    web_search_calls: int | None
    cost_nusd: int | None
    price_tier: str | None
    overrun: bool
    latency_ms: int | None
    api_response_id: str | None

    def to_trace(self) -> dict[str, Any]:
        if self.status != "settled" or self.cost_nusd is None:
            raise LedgerStateError("only settled calls have a trace line")
        return {
            "schema": TRACE_SCHEMA,
            "ts": self.settled_at,
            "call_id": self.call_id,
            "stage": self.stage,
            "agent_role": self.agent_role,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": float(nusd_to_usd(self.cost_nusd)),
            "reservation_usd": float(nusd_to_usd(self.reservation_nusd)),
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
            "input_kind": self.input_kind,
            "item_id": self.item_id,
            "outcome": self.outcome,
            "latency_ms": self.latency_ms,
            "api_response_id": self.api_response_id,
            # Exact integers for recomputation; the *_usd floats are for reading.
            "cost_nusd": self.cost_nusd,
            "reservation_nusd": self.reservation_nusd,
            "price_tier": self.price_tier,
            "overrun": self.overrun,
            "reserved_at": self.reserved_at,
            "est_input_tokens": self.est_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "web_search_calls": self.web_search_calls,
        }

    @classmethod
    def from_trace(cls, line: Mapping[str, Any]) -> CallRecord:
        if line.get("schema") != TRACE_SCHEMA:
            raise ValueError(f"unknown trace schema {line.get('schema')!r}")
        return cls(
            call_id=line["call_id"],
            status="settled",
            reserved_at=line["reserved_at"],
            settled_at=line["ts"],
            stage=line["stage"],
            agent_role=line["agent_role"],
            model=line["model"],
            reasoning_effort=line["reasoning_effort"],
            input_kind=line["input_kind"],
            item_id=line["item_id"],
            prompt_version=line["prompt_version"],
            prompt_sha256=line["prompt_sha256"],
            est_input_tokens=line["est_input_tokens"],
            max_output_tokens=line["max_output_tokens"],
            reservation_nusd=line["reservation_nusd"],
            outcome=line["outcome"],
            input_tokens=line["input_tokens"],
            cached_input_tokens=line["cached_input_tokens"],
            output_tokens=line["output_tokens"],
            reasoning_tokens=line["reasoning_tokens"],
            web_search_calls=line["web_search_calls"],
            cost_nusd=line["cost_nusd"],
            price_tier=line["price_tier"],
            overrun=bool(line["overrun"]),
            latency_ms=line["latency_ms"],
            api_response_id=line["api_response_id"],
        )


@dataclass(frozen=True, slots=True)
class Transfer:
    ts: str
    from_stage: str
    to_stage: str
    amount_nusd: int
    approved_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class Reconciliation:
    ts: str
    meta_total_nusd: int
    ledger_total_nusd: int
    difference_nusd: int  # ledger - meta; must never be negative
    period_start: str | None
    period_end: str | None
    meta_input_tokens: int | None
    meta_output_tokens: int | None
    ledger_input_tokens: int
    ledger_output_tokens: int
    note: str

    @property
    def undercounts(self) -> bool:
        return self.difference_nusd < 0


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def assert_no_secrets(text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise SecretInTraceError(
                f"refusing to write a trace line matching secret pattern {pattern.pattern!r}"
            )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('open', 'settled')),
    reserved_at TEXT NOT NULL,
    settled_at TEXT,
    stage TEXT NOT NULL,
    agent_role TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    input_kind TEXT NOT NULL,
    item_id TEXT,
    prompt_version TEXT,
    prompt_sha256 TEXT,
    est_input_tokens INTEGER NOT NULL,
    max_output_tokens INTEGER NOT NULL,
    reservation_nusd INTEGER NOT NULL CHECK (reservation_nusd >= 0),
    outcome TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    web_search_calls INTEGER,
    cost_nusd INTEGER CHECK (cost_nusd IS NULL OR cost_nusd >= 0),
    price_tier TEXT,
    overrun INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    api_response_id TEXT
);
CREATE INDEX IF NOT EXISTS calls_stage_status ON calls (stage, status);
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    from_stage TEXT NOT NULL,
    to_stage TEXT NOT NULL,
    amount_nusd INTEGER NOT NULL CHECK (amount_nusd > 0),
    approved_by TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    meta_total_nusd INTEGER NOT NULL,
    ledger_total_nusd INTEGER NOT NULL,
    difference_nusd INTEGER NOT NULL,
    period_start TEXT,
    period_end TEXT,
    meta_input_tokens INTEGER,
    meta_output_tokens INTEGER,
    ledger_input_tokens INTEGER NOT NULL,
    ledger_output_tokens INTEGER NOT NULL,
    note TEXT NOT NULL
);
"""

_CALL_COLUMNS = tuple(CallRecord.__dataclass_fields__)


@dataclass
class Ledger:
    """Thread-safe, process-safe budget ledger. Never stores a secret."""

    db_path: Path
    trace_path: Path
    config: BudgetConfig = DEFAULT_CONFIG
    clock: Callable[[], datetime] = utc_now
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _conn: sqlite3.Connection = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        self.trace_path = Path(self.trace_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.db_path, isolation_level=None, check_same_thread=False, timeout=30
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA_SQL)  # idempotent DDL; runs in its own transaction
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    @property
    def events_path(self) -> Path:
        return self.trace_path.with_name("budget-events.jsonl")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ transactions
    @contextlib.contextmanager
    def _write_txn(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                yield cur
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            else:
                cur.execute("COMMIT")
            finally:
                cur.close()

    # ------------------------------------------------------------------ limits
    def _transfer_delta(self, cur: sqlite3.Cursor, stage: str) -> int:
        row = cur.execute(
            "SELECT COALESCE(SUM(CASE WHEN to_stage = ? THEN amount_nusd ELSE 0 END), 0) "
            "- COALESCE(SUM(CASE WHEN from_stage = ? THEN amount_nusd ELSE 0 END), 0) "
            "FROM transfers",
            (stage, stage),
        ).fetchone()
        return int(row[0])

    def _effective_limit(self, cur: sqlite3.Cursor, stage: str) -> int:
        return self.config.stage_limits_nusd.get(stage, 0) + self._transfer_delta(cur, stage)

    @staticmethod
    def _committed(cur: sqlite3.Cursor, stage: str | None = None) -> int:
        sql = (
            "SELECT COALESCE(SUM(CASE WHEN status = 'settled' THEN cost_nusd "
            "ELSE reservation_nusd END), 0) FROM calls"
        )
        params: tuple[str, ...] = ()
        if stage is not None:
            sql += " WHERE stage = ?"
            params = (stage,)
        return int(cur.execute(sql, params).fetchone()[0])

    # ------------------------------------------------------------------ reserve / settle
    def reserve(self, request: CallRequest) -> Reservation:
        """Reserve the worst-case cost of ``request`` or raise BudgetExceeded."""
        amount = worst_case_cost(
            request.model,
            input_tokens_upper_bound=request.est_input_tokens,
            max_output_tokens=request.max_output_tokens,
        )
        call_id = uuid.uuid4().hex
        now = self.clock()
        stage = request.stage.value
        with self._write_txn() as cur:
            stage_committed = self._committed(cur, stage)
            stage_limit = self._effective_limit(cur, stage)
            if stage_committed + amount > stage_limit:
                raise StageLimitExceeded(
                    scope=f"stage:{stage}",
                    limit_nusd=stage_limit,
                    committed_nusd=stage_committed,
                    requested_nusd=amount,
                )
            global_committed = self._committed(cur)
            if global_committed + amount > self.config.global_cap_nusd:
                raise GlobalCapExceeded(
                    scope="global",
                    limit_nusd=self.config.global_cap_nusd,
                    committed_nusd=global_committed,
                    requested_nusd=amount,
                )
            cur.execute(
                "INSERT INTO calls (call_id, status, reserved_at, stage, agent_role, model, "
                "reasoning_effort, input_kind, item_id, prompt_version, prompt_sha256, "
                "est_input_tokens, max_output_tokens, reservation_nusd) "
                "VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    call_id,
                    _iso(now),
                    stage,
                    request.agent_role,
                    request.model,
                    request.reasoning_effort,
                    request.input_kind.value,
                    request.item_id,
                    request.prompt_version,
                    request.prompt_sha256,
                    request.est_input_tokens,
                    request.max_output_tokens,
                    amount,
                ),
            )
        return Reservation(
            call_id=call_id, request=request, reservation_nusd=amount, reserved_at=now
        )

    def settle(
        self,
        reservation: Reservation | str,
        *,
        outcome: Outcome,
        usage: Usage | None,
        latency_ms: int | None = None,
        api_response_id: str | None = None,
    ) -> CallRecord:
        """Close a reservation. Missing usage or a failed call keeps the full reservation."""
        outcome = Outcome(outcome)
        if outcome is Outcome.ORPHANED:
            raise LedgerStateError("ORPHANED is set only by expire_open")
        if api_response_id is not None and not SAFE_ID_PATTERN.match(api_response_id):
            raise ValueError("api_response_id has unexpected characters")
        call_id = reservation if isinstance(reservation, str) else reservation.call_id
        with self._write_txn() as cur:
            row = cur.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            if row is None:
                raise LedgerStateError(f"unknown call_id {call_id}")
            if row["status"] != "open":
                raise LedgerStateError(f"call {call_id} is already settled")
            reserved = int(row["reservation_nusd"])
            breakdown = None
            if usage is not None:
                breakdown = actual_cost(
                    row["model"],
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    output_tokens=usage.output_tokens,
                    reasoning_tokens=usage.reasoning_tokens,
                    web_search_calls=usage.web_search_calls,
                )
            if breakdown is not None and outcome in USAGE_TRUSTED_OUTCOMES:
                cost = breakdown.total_nusd
            else:
                cost = max(reserved, breakdown.total_nusd if breakdown else 0)
            self._close_row(
                cur,
                call_id=call_id,
                outcome=outcome,
                usage=usage,
                cost=cost,
                price_tier=breakdown.tier.value if breakdown else None,
                overrun=cost > reserved,
                latency_ms=latency_ms,
                api_response_id=api_response_id,
            )
            record = self._row_to_record(
                cur.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
            )
        self._append_jsonl(self.trace_path, record.to_trace())
        return record

    def expire_open(self, older_than: timedelta) -> list[CallRecord]:
        """Close reservations left open by a crash. They are kept as spent in full."""
        cutoff = _iso(self.clock() - older_than)
        closed: list[CallRecord] = []
        with self._write_txn() as cur:
            rows = cur.execute(
                "SELECT call_id, reservation_nusd FROM calls "
                "WHERE status = 'open' AND reserved_at <= ?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                self._close_row(
                    cur,
                    call_id=row["call_id"],
                    outcome=Outcome.ORPHANED,
                    usage=None,
                    cost=int(row["reservation_nusd"]),
                    price_tier=None,
                    overrun=False,
                    latency_ms=None,
                    api_response_id=None,
                )
                closed.append(
                    self._row_to_record(
                        cur.execute(
                            "SELECT * FROM calls WHERE call_id = ?", (row["call_id"],)
                        ).fetchone()
                    )
                )
        for record in closed:
            self._append_jsonl(self.trace_path, record.to_trace())
        return closed

    def _close_row(
        self,
        cur: sqlite3.Cursor,
        *,
        call_id: str,
        outcome: Outcome,
        usage: Usage | None,
        cost: int,
        price_tier: str | None,
        overrun: bool,
        latency_ms: int | None,
        api_response_id: str | None,
    ) -> None:
        cur.execute(
            "UPDATE calls SET status = 'settled', settled_at = ?, outcome = ?, "
            "input_tokens = ?, cached_input_tokens = ?, output_tokens = ?, "
            "reasoning_tokens = ?, web_search_calls = ?, cost_nusd = ?, price_tier = ?, "
            "overrun = ?, latency_ms = ?, api_response_id = ? "
            "WHERE call_id = ? AND status = 'open'",
            (
                _iso(self.clock()),
                outcome.value,
                usage.input_tokens if usage else None,
                usage.cached_input_tokens if usage else None,
                usage.output_tokens if usage else None,
                usage.reasoning_tokens if usage else None,
                usage.web_search_calls if usage else None,
                cost,
                price_tier,
                int(overrun),
                latency_ms,
                api_response_id,
                call_id,
            ),
        )
        if cur.rowcount != 1:
            raise LedgerStateError(f"call {call_id} changed state during settlement")

    # ------------------------------------------------------------------ transfers
    def transfer(
        self, *, from_stage: Stage, to_stage: Stage, amount_nusd: int, approved_by: str, reason: str
    ) -> Transfer:
        """Move headroom between stages. Needs a named human approver; never raises the cap."""
        from_stage, to_stage = Stage(from_stage), Stage(to_stage)
        if from_stage is to_stage:
            raise ValueError("from_stage and to_stage must differ")
        if amount_nusd <= 0:
            raise ValueError("transfer amount must be positive")
        if not approved_by.strip() or not reason.strip():
            raise ValueError("a transfer needs approved_by and reason")
        transfer = Transfer(
            ts=_iso(self.clock()),
            from_stage=from_stage.value,
            to_stage=to_stage.value,
            amount_nusd=amount_nusd,
            approved_by=approved_by.strip(),
            reason=reason.strip(),
        )
        with self._write_txn() as cur:
            headroom = self._effective_limit(cur, from_stage.value) - self._committed(
                cur, from_stage.value
            )
            if amount_nusd > headroom:
                raise StageLimitExceeded(
                    scope=f"transfer-from:{from_stage.value}",
                    limit_nusd=headroom,
                    committed_nusd=0,
                    requested_nusd=amount_nusd,
                )
            cur.execute(
                "INSERT INTO transfers (ts, from_stage, to_stage, amount_nusd, approved_by, "
                "reason) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    transfer.ts,
                    transfer.from_stage,
                    transfer.to_stage,
                    transfer.amount_nusd,
                    transfer.approved_by,
                    transfer.reason,
                ),
            )
        self._append_jsonl(
            self.events_path, {"schema": EVENT_SCHEMA, "event": "transfer", **asdict(transfer)}
        )
        return transfer

    # ------------------------------------------------------------------ reconciliation
    def reconcile(
        self,
        *,
        meta_total_nusd: int,
        note: str = "",
        period_start: str | None = None,
        period_end: str | None = None,
        meta_input_tokens: int | None = None,
        meta_output_tokens: int | None = None,
    ) -> Reconciliation:
        """Record Meta's Usage-page total next to the ledger total. Ledger must be >= Meta."""
        if meta_total_nusd < 0:
            raise ValueError("meta total must be >= 0")
        with self._write_txn() as cur:
            ledger_total = self._committed(cur)
            tokens = cur.execute(
                "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) "
                "FROM calls WHERE status = 'settled'"
            ).fetchone()
            rec = Reconciliation(
                ts=_iso(self.clock()),
                meta_total_nusd=meta_total_nusd,
                ledger_total_nusd=ledger_total,
                difference_nusd=ledger_total - meta_total_nusd,
                period_start=period_start,
                period_end=period_end,
                meta_input_tokens=meta_input_tokens,
                meta_output_tokens=meta_output_tokens,
                ledger_input_tokens=int(tokens[0]),
                ledger_output_tokens=int(tokens[1]),
                note=note,
            )
            cur.execute(
                "INSERT INTO reconciliations (ts, meta_total_nusd, ledger_total_nusd, "
                "difference_nusd, period_start, period_end, meta_input_tokens, "
                "meta_output_tokens, ledger_input_tokens, ledger_output_tokens, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.ts,
                    rec.meta_total_nusd,
                    rec.ledger_total_nusd,
                    rec.difference_nusd,
                    rec.period_start,
                    rec.period_end,
                    rec.meta_input_tokens,
                    rec.meta_output_tokens,
                    rec.ledger_input_tokens,
                    rec.ledger_output_tokens,
                    rec.note,
                ),
            )
        self._append_jsonl(
            self.events_path,
            {
                "schema": EVENT_SCHEMA,
                "event": "reconciliation",
                **asdict(rec),
                "undercounts": rec.undercounts,
            },
        )
        return rec

    # ------------------------------------------------------------------ reads
    def calls(self, *, status: str | None = None) -> list[CallRecord]:
        sql = "SELECT * FROM calls"
        params: tuple[str, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY reserved_at, call_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def transfers(self) -> list[Transfer]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, from_stage, to_stage, amount_nusd, approved_by, reason "
                "FROM transfers ORDER BY id"
            ).fetchall()
        return [Transfer(**dict(r)) for r in rows]

    def reconciliations(self) -> list[Reconciliation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, meta_total_nusd, ledger_total_nusd, difference_nusd, period_start, "
                "period_end, meta_input_tokens, meta_output_tokens, ledger_input_tokens, "
                "ledger_output_tokens, note FROM reconciliations ORDER BY id"
            ).fetchall()
        return [Reconciliation(**dict(r)) for r in rows]

    def effective_stage_limits(self) -> dict[str, int]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                return {s.value: self._effective_limit(cur, s.value) for s in Stage}
            finally:
                cur.close()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CallRecord:
        data = {col: row[col] for col in _CALL_COLUMNS}
        data["overrun"] = bool(data["overrun"])
        return CallRecord(**data)

    # ------------------------------------------------------------------ JSONL mirror
    def _append_jsonl(self, path: Path, payload: Mapping[str, Any]) -> None:
        line = json.dumps(payload, sort_keys=False, separators=(",", ":"), ensure_ascii=True)
        assert_no_secrets(line)
        with self._lock, path.open("a", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def read_events(path: Path) -> tuple[list[Transfer], list[Reconciliation]]:
    """Read the budget-events trace (transfers and reconciliations)."""
    transfers: list[Transfer] = []
    recs: list[Reconciliation] = []
    if not Path(path).exists():
        return transfers, recs
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        data = json.loads(raw)
        if data.get("schema") != EVENT_SCHEMA:
            raise ValueError(f"unknown event schema {data.get('schema')!r}")
        kind = data.pop("event")
        data.pop("schema")
        if kind == "transfer":
            transfers.append(Transfer(**data))
        elif kind == "reconciliation":
            data.pop("undercounts", None)
            recs.append(Reconciliation(**data))
        else:
            raise ValueError(f"unknown budget event {kind!r}")
    return transfers, recs


def effective_limits(config: BudgetConfig, transfers: list[Transfer]) -> dict[str, int]:
    limits = {s.value: config.stage_limits_nusd.get(s.value, 0) for s in Stage}
    for t in transfers:
        limits[t.from_stage] -= t.amount_nusd
        limits[t.to_stage] += t.amount_nusd
    return limits


def read_trace(path: Path) -> list[CallRecord]:
    """Read a calls trace. Duplicate call_ids are an error (the trace is append-only)."""
    records: list[CallRecord] = []
    seen: set[str] = set()
    if not Path(path).exists():
        return records
    for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        record = CallRecord.from_trace(json.loads(raw))
        if record.call_id in seen:
            raise ValueError(f"{path}:{n}: duplicate call_id {record.call_id}")
        seen.add(record.call_id)
        records.append(record)
    return records
