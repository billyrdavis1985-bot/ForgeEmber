"""Run one Ember agent: strict structured output in, validated Pydantic object out.

An agent never acts. It returns a typed proposal, or an error string, plus the ledger record
of what the call cost. Budget refusals propagate (``BudgetExceeded``) so the caller can stop.
"""

from __future__ import annotations

import base64
import io
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from forgeember.budget.ledger import BudgetExceeded, CallRecord, InputKind
from forgeember.ember.client import (
    EmberAPIError,
    EmberClient,
    TransportError,
    estimate_text_input_tokens,
)
from forgeember.ember.prompts import Prompt
from forgeember.schemas import text_format

TEXT_BLOCKS = frozenset({"input_text"})
MAX_RETRY_OUTPUT_TOKENS = 8192
MAX_TOOL_ROUNDS = 3  # model turns per tool agent, each a separately budgeted call
MAX_TOOL_CALLS_PER_ROUND = 4  # further calls in one turn get an error result, not a lookup
_CALL_ID = re.compile(r"^[\x21-\x7e]{1,64}$")  # Meta: a call_id is 1-64 characters
MEDIA_COUNT_MARGIN = 1.10  # +10% over the measured count
MEDIA_COUNT_SLACK = 256
# Static allowance per media block. Used for the count call's own reservation and as the
# fallback bound when the count endpoint fails. Generous on purpose: the count endpoint
# is the primary bound, and settlement records actuals, so this only gates, never bills.
STATIC_MEDIA_TOKEN_ALLOWANCE = 200_000
# Per-kind bounds that replace token-count calls for PDFs and images. They only gate the
# reservation: settlement always records the API's actual usage, and an overrun is flagged.
# Measured 2026-11-23 (first live run): a one-page fixture PDF added ~2,450 input tokens
# (text layer + page image). Meta's docs put a ~1280px image at ~1,300-1,500 tokens.
PDF_TOKENS_PER_PAGE = 4_000
PDF_PAGE_IMAGES_MAX = 50  # Meta keeps page images for the first 50 pages
IMAGE_TOKENS = 6_000


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
    retryable: bool = False  # transient (5xx, 429, network): worth another pass later
    status_code: int | None = None  # HTTP status of an API error, if any
    # Calls made before ``record`` (an output-limit retry, or tool rounds): the ledger has
    # them all, and the item's receipt must too.
    earlier_records: tuple[CallRecord, ...] = ()


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
    input_kind: InputKind,
) -> int:
    """Measure with Meta's count endpoint, then add a margin. Live only.

    The count call is budgeted too: it reserves a static outer bound first, then
    settles as billed input (see EmberClient.count_input_tokens).
    """
    static = static_estimator(STATIC_MEDIA_TOKEN_ALLOWANCE)(
        client,
        input=input,
        instructions=instructions,
        fmt=fmt,
        role=role,
        item_id=item_id,
        input_kind=input_kind,
    )
    n = client.count_input_tokens(
        input=input,
        agent_role=role,
        input_kind=input_kind,
        est_input_tokens=static,
        instructions=instructions,
        text_format=fmt,
        item_id=item_id,
    )
    return int(n * MEDIA_COUNT_MARGIN) + MEDIA_COUNT_SLACK


def _pdf_bound(file_data: str) -> int:
    """Upper bound for one inline PDF: its text layer bytes + a per-page image allowance."""
    try:
        from pypdf import PdfReader

        raw = base64.b64decode(file_data.split(",", 1)[1], validate=False)
        reader = PdfReader(io.BytesIO(raw))
        pages = len(reader.pages)
        text = "".join((pg.extract_text() or "") for pg in reader.pages[:100])
    except Exception:  # unreadable here: fall back to the generous outer bound
        return STATIC_MEDIA_TOKEN_ALLOWANCE
    return len(text.encode("utf-8")) + PDF_TOKENS_PER_PAGE * max(1, min(pages, PDF_PAGE_IMAGES_MAX))


def media_block_bound(block: Mapping[str, Any]) -> int | None:
    """Static token bound for one media block, or None if only the count endpoint can tell."""
    kind = block.get("type")
    if kind == "input_file" and str(block.get("file_data", "")).startswith("data:application/pdf"):
        return _pdf_bound(str(block["file_data"]))
    if kind == "input_image":
        return IMAGE_TOKENS
    return None  # audio, video, files by id/url: measure instead


def bounded_estimator(
    client: EmberClient,
    *,
    input: Sequence[Mapping[str, Any]],
    instructions: str,
    fmt: Mapping[str, Any],
    role: str,
    item_id: str | None,
    input_kind: InputKind,
) -> int:
    """Default: static per-kind bounds (no extra API call); count only what can't be bounded."""
    blocks = [b for item in input for b in (item.get("content") or []) if isinstance(b, Mapping)]
    bounds = [media_block_bound(b) for b in blocks if b.get("type") != "input_text"]
    if any(b is None for b in bounds):
        return counting_estimator(
            client,
            input=input,
            instructions=instructions,
            fmt=fmt,
            role=role,
            item_id=item_id,
            input_kind=input_kind,
        )
    text_only = static_estimator(0)(
        client, input=input, instructions=instructions, fmt=fmt, role=role, item_id=item_id
    )
    return text_only + sum(b for b in bounds if b is not None)


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
        input_kind: InputKind | None = None,  # accepted for estimator uniformity; unused
    ) -> int:
        texts = [instructions, json.dumps(fmt)]
        media = 0
        for item in input:
            if not isinstance(item, Mapping):
                raise ValueError("input items must be mappings")
            content = item.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, Sequence):
                for block in content:
                    if not isinstance(block, Mapping):
                        raise ValueError("input content blocks must be mappings")
                    if block.get("type") == "input_text":
                        texts.append(str(block.get("text", "")))
                    else:
                        media += 1
            elif content is None:
                continue
            else:
                raise ValueError("input item content must be a string or a list of blocks")
        return estimate_text_input_tokens(texts, items=len(input)) + per_media_part * media

    return estimate


def run_agent[T: BaseModel](
    client: EmberClient,
    spec: AgentSpec[T],
    *,
    input_items: Sequence[Mapping[str, Any]],
    input_kind: InputKind,
    item_id: str | None,
    estimator: Estimator = bounded_estimator,
) -> AgentResult[T]:
    fmt = text_format(spec.model, spec.schema_name)
    est = None
    estimator_note = None
    if has_media(input_items):
        try:
            est = estimator(
                client,
                input=input_items,
                instructions=spec.prompt.text,
                fmt=fmt,
                role=spec.role,
                item_id=item_id,
                input_kind=input_kind,
            )
        except BudgetExceeded:
            raise
        except (EmberAPIError, TransportError):
            # The count endpoint failed after keeping its own reservation: fall back
            # to a static bound so screening still completes, and say so. The call
            # below is still reserved and settled exactly.
            est = static_estimator(STATIC_MEDIA_TOKEN_ALLOWANCE)(
                client,
                input=input_items,
                instructions=spec.prompt.text,
                fmt=fmt,
                role=spec.role,
                item_id=item_id,
                input_kind=input_kind,
            )
            estimator_note = f"{spec.role}: token count failed, used static bound"

    def call(max_output_tokens: int) -> Any:
        return client.respond(
            input=input_items,
            instructions=spec.prompt.text,
            agent_role=spec.role,
            input_kind=input_kind,
            reasoning_effort=spec.effort,
            max_output_tokens=max_output_tokens,
            text_format=fmt,
            est_input_tokens=est,
            item_id=item_id,
            prompt_version=spec.prompt.version,
            prompt_sha256=spec.prompt.sha256,
            prompt_cache_key=f"forgeember-{spec.prompt.version}",
        )

    earlier: list[CallRecord] = []
    try:
        result = call(spec.max_output_tokens)
        if result.status == "incomplete":  # ran out of output budget: one retry with double
            earlier.append(result.record)
            result = call(min(spec.max_output_tokens * 2, MAX_RETRY_OUTPUT_TOKENS))
    except (EmberAPIError, TransportError) as exc:  # ledger already kept the reservation
        return _api_failure(spec, exc, earlier)
    return _final(spec, result, earlier, estimator_note)


def _api_failure(
    spec: AgentSpec, exc: EmberAPIError | TransportError, earlier: list[CallRecord]
) -> AgentResult:
    if isinstance(exc, EmberAPIError):
        transient = exc.status_code >= 500 or exc.status_code == 429
        status: int | None = exc.status_code
    else:
        transient, status = True, None
    return AgentResult(
        None,
        f"{spec.role}: {type(exc).__name__}: {exc}",
        None,
        transient,
        status,
        earlier_records=tuple(earlier),
    )


def _final[T: BaseModel](
    spec: AgentSpec[T], result: Any, earlier: list[CallRecord], note: str | None = None
) -> AgentResult[T]:
    """Validate a final answer. Every call made on the way is kept with the result."""
    kept = tuple(earlier)
    if result.status != "completed":
        return AgentResult(
            None, f"{spec.role}: status {result.status}", result.record, earlier_records=kept
        )
    try:
        value = spec.model.model_validate_json(result.text)
    except ValidationError as exc:
        return AgentResult(
            None,
            f"{spec.role}: invalid output ({exc.error_count()} errors)",
            result.record,
            earlier_records=kept,
        )
    return AgentResult(value, note, result.record, earlier_records=kept)


class Toolbox(Protocol):
    """Read-only tools the model may call. ``run`` never raises on bad model input."""

    @property
    def definitions(self) -> list[dict[str, Any]]: ...

    def run(self, name: str, raw_arguments: str) -> dict[str, Any]: ...


def function_calls(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        item
        for item in payload.get("output") or []
        if isinstance(item, Mapping) and item.get("type") == "function_call"
    ]


def run_tool_agent[T: BaseModel](
    client: EmberClient,
    spec: AgentSpec[T],
    *,
    input_items: Sequence[Mapping[str, Any]],
    input_kind: InputKind,
    item_id: str | None,
    toolbox: Toolbox,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AgentResult[T]:
    """A function-calling loop. Ember asks for lookups; code runs them and sends the results back.

    Stateless (``store: false``), so each round resends the conversation: the item, then every
    function_call and its function_call_output. Each round is its own reserved, settled ledger
    call. ``tool_choice`` can only be "auto" on Ember, so a round may end with tool calls or a
    final answer; after ``max_rounds`` rounds of calls the agent fails (screening incomplete).
    Text-only input: tool agents never take media, so no token estimator is needed.
    """
    if has_media(input_items):
        raise ValueError("tool agents take text-only input")
    fmt = text_format(spec.model, spec.schema_name)
    conversation: list[Mapping[str, Any]] = list(input_items)
    earlier: list[CallRecord] = []
    for _ in range(max_rounds):
        try:
            result = client.respond(
                input=conversation,
                instructions=spec.prompt.text,
                agent_role=spec.role,
                input_kind=input_kind,
                reasoning_effort=spec.effort,
                max_output_tokens=spec.max_output_tokens,
                text_format=fmt,
                tools=toolbox.definitions,
                item_id=item_id,
                prompt_version=spec.prompt.version,
                prompt_sha256=spec.prompt.sha256,
                prompt_cache_key=f"forgeember-{spec.prompt.version}",
            )
        except (EmberAPIError, TransportError) as exc:
            return _api_failure(spec, exc, earlier)
        calls = function_calls(result.payload)
        if not calls:
            return _final(spec, result, earlier)
        earlier.append(result.record)
        for n, call in enumerate(calls):
            call_id = call.get("call_id")
            if not (isinstance(call_id, str) and _CALL_ID.match(call_id)):
                return AgentResult(
                    None,
                    f"{spec.role}: malformed tool call id",
                    None,
                    earlier_records=tuple(earlier),
                )
            name = str(call.get("name") or "")
            raw = call.get("arguments") if isinstance(call.get("arguments"), str) else ""
            out = (
                toolbox.run(name, raw)
                if n < MAX_TOOL_CALLS_PER_ROUND
                else {"error": f"at most {MAX_TOOL_CALLS_PER_ROUND} lookups per turn"}
            )
            conversation.append(
                {"type": "function_call", "call_id": call_id, "name": name, "arguments": raw}
            )
            conversation.append(
                {"type": "function_call_output", "call_id": call_id, "output": json.dumps(out)}
            )
    return AgentResult(
        None,
        f"{spec.role}: still calling tools after {max_rounds} rounds",
        None,
        earlier_records=tuple(earlier),
    )


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
