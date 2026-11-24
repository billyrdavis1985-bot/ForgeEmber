from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from forgeember.budget.ledger import DEFAULT_CONFIG, BudgetConfig, CallRequest, Ledger
from forgeember.ember.client import TransportError, TransportResponse


class FakeClock:
    """Deterministic clock: each read advances by one second."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 23, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_ledger(tmp_path, clock) -> Callable[..., Ledger]:
    counter = itertools.count()
    opened: list[Ledger] = []

    def _make(config: BudgetConfig = DEFAULT_CONFIG) -> Ledger:
        n = next(counter)
        ledger = Ledger(
            db_path=tmp_path / f"l{n}" / "ledger.sqlite",
            trace_path=tmp_path / f"l{n}" / "traces" / "calls.jsonl",
            config=config,
            clock=clock,
        )
        opened.append(ledger)
        return ledger

    yield _make
    for ledger in opened:
        ledger.close()


@pytest.fixture
def ledger(make_ledger) -> Ledger:
    return make_ledger()


def req(**overrides: Any) -> CallRequest:
    base: dict[str, Any] = {
        "stage": "dev",
        "model": "muse-ember-1.0",
        "agent_role": "document",
        "input_kind": "pdf",
        "reasoning_effort": "low",
        "est_input_tokens": 10_000,
        "max_output_tokens": 2_000,
        "item_id": "fixture-001",
        "prompt_version": "doc-v1",
        "prompt_sha256": "a" * 64,
    }
    base.update(overrides)
    return CallRequest(**base)


def responses_payload(
    *,
    input_tokens: int = 1200,
    cached: int = 1000,
    output_tokens: int = 300,
    reasoning: int = 120,
    status: str = "completed",
    text: str = "ok",
    response_id: str = "resp_abc123",
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "status": status,
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


def function_call_payload(
    calls: list[tuple[str, str, dict[str, Any]]], *, response_id: str = "resp_tool123"
) -> dict[str, Any]:
    """A Responses API body whose output asks for tool calls: (call_id, name, arguments)."""
    payload = responses_payload(text="", response_id=response_id, output_tokens=60, reasoning=20)
    payload["output"] = [{"type": "reasoning", "id": "rs_1", "summary": []}] + [
        {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(args),
            "status": "completed",
        }
        for call_id, name, args in calls
    ]
    return payload


@dataclass
class FakeTransport:
    """Scripted transport. Each item is a TransportResponse or an exception to raise."""

    script: list[TransportResponse | Exception] = field(default_factory=list)
    calls: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)

    def post_json(self, path: str, body: Mapping[str, Any]) -> TransportResponse:
        self.calls.append((path, body))
        if not self.script:
            raise AssertionError("FakeTransport called more times than scripted")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def ok_response(**kwargs: Any) -> TransportResponse:
    return TransportResponse(status_code=200, payload=responses_payload(**kwargs))


def network_error() -> TransportError:
    return TransportError("ReadTimeout")
