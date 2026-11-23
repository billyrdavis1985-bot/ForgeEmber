"""Run one Ember agent: strict structured output in, validated Pydantic object out.

An agent never acts. It returns a typed proposal, or an error string, plus the ledger record
of what the call cost. Budget refusals propagate (``BudgetExceeded``) so the caller can stop.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, ValidationError

from forgeember.budget.ledger import CallRecord, InputKind
from forgeember.ember.client import (
    EmberAPIError,
    EmberClient,
    TransportError,
    estimate_text_input_tokens,
)
from forgeember.ember.prompts import Prompt
from forgeember.schemas import text_format

TEXT_BLOCKS = frozenset({"input_text"})
MEDIA_COUNT_MARGIN = 1.10  # +10% over the measured count
MEDIA_COUNT_SLACK = 256


@dataclass(frozen=True, slots=True)
class AgentSpec[T: BaseModel]:
    role: str  # agent_role in the ledger
    prompt: Prompt
    model: type[T]
    schema_name: str
    effort: str
    max_output_tokens: int

    def with_effort(self, effort: str) -> AgentSpec[T]:
        return replace(self, effort=effort)


@dataclass(frozen=True, slots=True)
class AgentResult[T: BaseModel]:
    value: T | None
    error: str | None
    record: CallRecord | None


def has_media(items: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        block.get("type") not in TEXT_BLOCKS
        for item in items
        for block in (item.get("content") or [])
        if isinstance(block, Mapping)
    )


# An estimator returns an UPPER BOUND on input tokens for a request with media parts.
Estimator = Callable[..., int]


def counting_estimator(
    client: EmberClient,
    *,
    input: Sequence[Mapping[str, Any]],
    instructions: str,
    fmt: Mapping[str, Any],
    role: str,
    item_id: str | None,
) -> int:
    """Measure with Meta's count endpoint, then add a margin. Live only."""
    n = client.count_input_tokens(
        input=input, agent_role=role, instructions=instructions, text_format=fmt, item_id=item_id
    )
    return int(n * MEDIA_COUNT_MARGIN) + MEDIA_COUNT_SLACK


def static_estimator(per_media_part: int) -> Estimator:
    """Offline bound for tests and dry runs: text bytes + a fixed allowance per media block."""

    def estimate(
        client: EmberClient,
        *,
        input: Sequence[Mapping[str, Any]],
        instructions: str,
        fmt: Mapping[str, Any],
        role: str,
        item_id: str | None,
    ) -> int:
        texts = [instructions, json.dumps(fmt)]
        media = 0
        for item in input:
            for block in item.get("content") or []:
                if block.get("type") == "input_text":
                    texts.append(block.get("text", ""))
                else:
                    media += 1
        return estimate_text_input_tokens(texts, items=len(input)) + per_media_part * media

    return estimate


def run_agent[T: BaseModel](
    client: EmberClient,
    spec: AgentSpec[T],
    *,
    input_items: Sequence[Mapping[str, Any]],
    input_kind: InputKind,
    item_id: str | None,
    estimator: Estimator = counting_estimator,
) -> AgentResult[T]:
    fmt = text_format(spec.model, spec.schema_name)
    est = None
    if has_media(input_items):
        est = estimator(
            client,
            input=input_items,
            instructions=spec.prompt.text,
            fmt=fmt,
            role=spec.role,
            item_id=item_id,
        )
    try:
        result = client.respond(
            input=input_items,
            instructions=spec.prompt.text,
            agent_role=spec.role,
            input_kind=input_kind,
            reasoning_effort=spec.effort,
            max_output_tokens=spec.max_output_tokens,
            text_format=fmt,
            est_input_tokens=est,
            item_id=item_id,
            prompt_version=spec.prompt.version,
            prompt_sha256=spec.prompt.sha256,
            prompt_cache_key=f"forgeember-{spec.prompt.version}",
        )
    except (EmberAPIError, TransportError) as exc:  # ledger already kept the reservation
        return AgentResult(None, f"{spec.role}: {type(exc).__name__}: {exc}", None)
    if result.status != "completed":
        return AgentResult(None, f"{spec.role}: status {result.status}", result.record)
    try:
        value = spec.model.model_validate_json(result.text)
    except ValidationError as exc:
        return AgentResult(
            None, f"{spec.role}: invalid output ({exc.error_count()} errors)", result.record
        )
    return AgentResult(value, None, result.record)


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
