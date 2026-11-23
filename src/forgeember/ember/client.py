"""The ONLY module in ForgeEmber that touches the network.

Every call follows one path, with no exceptions:

    validate request -> ledger.reserve (may refuse) -> POST /v1/responses -> ledger.settle

* Live HTTP needs an explicit ``live=True`` and is refused when the ``CI`` env var is set.
* ``reasoning_effort`` is required. Muse Ember defaults to "max" when it is omitted, which is
  the most expensive setting. "none" returns HTTP 400 on Ember, so we reject it locally.
* Hosted tools (e.g. ``web_search``, $2.50 / 1K queries) are refused. Only client-executed
  ``function`` / ``custom`` tools are allowed, and policy intercepts those.
* ``store`` is always false: stateless requests, so ``usage`` covers the whole input.
* The API key is read from ``MODEL_API_KEY`` at request time, and never logged or returned.
  Error messages carry only the HTTP status and the API's error type/code.

API facts verified 2026-11-23, with sources in docs/META_API_NOTES.md.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from forgeember.budget.ledger import (
    SAFE_ID_PATTERN,
    CallRecord,
    CallRequest,
    InputKind,
    Ledger,
    LedgerStateError,
    Outcome,
    Stage,
    Usage,
)
from forgeember.config import API_BASE_URL, API_KEY_ENV, EMBER_MODEL

ALLOWED_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})
MIN_MAX_OUTPUT_TOKENS = 16  # documented minimum for max_output_tokens on the Responses API
ALLOWED_TOOL_TYPES = frozenset({"function", "custom"})
TEXT_BLOCK_TYPES = frozenset({"input_text", "output_text"})
# Upper-bound slack for the prompt template around our text. Meta reports ~110 input tokens
# for a short text request (dev.meta.ai/docs/media-understanding); we reserve far more.
BASE_OVERHEAD_TOKENS = 512
PER_ITEM_OVERHEAD_TOKENS = 32


class LiveCallsDisabled(RuntimeError):
    """A live call was attempted without --live, or inside CI."""


class TransportError(RuntimeError):
    """Network-level failure: no HTTP response (timeout, connection reset, DNS)."""


class EmberAPIError(RuntimeError):
    def __init__(self, status_code: int, error_type: str | None, code: str | None) -> None:
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        super().__init__(f"Meta Model API error: HTTP {status_code} type={error_type} code={code}")


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    payload: Mapping[str, Any] | None
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def post_json(self, path: str, body: Mapping[str, Any]) -> TransportResponse: ...


def _in_ci(environ: Mapping[str, str]) -> bool:
    return environ.get("CI", "").strip().lower() not in {"", "0", "false", "no"}


class HttpxTransport:
    """Real HTTP transport. Construction itself requires ``live=True`` and refuses in CI."""

    def __init__(
        self,
        *,
        live: bool,
        base_url: str = API_BASE_URL,
        timeout_s: float = 120.0,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        if not live:
            raise LiveCallsDisabled("live HTTP requires an explicit live=True (CLI: --live)")
        if _in_ci(env):
            raise LiveCallsDisabled("live calls are never made in CI")
        if not env.get(API_KEY_ENV):
            raise LiveCallsDisabled(f"{API_KEY_ENV} is not set in this shell")
        self._environ = env
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def post_json(self, path: str, body: Mapping[str, Any]) -> TransportResponse:
        headers = {
            "Authorization": f"Bearer {self._environ[API_KEY_ENV]}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._client.post(path, json=body, headers=headers)
        except httpx.HTTPError as exc:  # no response: billing unknown
            raise TransportError(type(exc).__name__) from None
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        keep = {
            k: v
            for k, v in resp.headers.items()
            if k.lower().startswith(("x-ratelimit", "retry-after"))
        }
        return TransportResponse(status_code=resp.status_code, payload=payload, headers=keep)

    def close(self) -> None:
        self._client.close()


def estimate_text_input_tokens(texts: Sequence[str], *, items: int = 1) -> int:
    """Upper bound on input tokens for text-only requests.

    A BPE token covers at least one byte of text, so UTF-8 byte length bounds the text tokens.
    Template and role markers are bounded by the overhead constants.
    """
    text_bytes = sum(len(t.encode("utf-8")) for t in texts)
    return text_bytes + BASE_OVERHEAD_TOKENS + PER_ITEM_OVERHEAD_TOKENS * max(items, 1)


def _collect_text(input_: str | Sequence[Mapping[str, Any]]) -> tuple[list[str], int, bool]:
    """Return (texts, item_count, text_only) for a Responses ``input`` value."""
    if isinstance(input_, str):
        return [input_], 1, True
    texts: list[str] = []
    text_only = True
    for item in input_:
        if not isinstance(item, Mapping):
            raise ValueError(f"input items must be mappings, got {type(item).__name__}")
        content = item.get("content")
        if isinstance(content, str):
            texts.append(content)
            continue
        if item.get("type") == "function_call_output":
            texts.append(str(item.get("output", "")))
            continue
        if item.get("type") == "function_call":
            texts.append(str(item.get("arguments", "")) + str(item.get("name", "")))
            continue
        if content is None:
            continue
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
            raise ValueError("input item content must be a string or a list of blocks")
        for block in content:
            if not isinstance(block, Mapping):
                raise ValueError("input content blocks must be mappings")
            if block.get("type") in TEXT_BLOCK_TYPES:
                texts.append(str(block.get("text", "")))
            else:
                text_only = False
    return texts, len(input_), text_only


def parse_usage(payload: Mapping[str, Any] | None) -> Usage | None:
    """Extract usage from a Responses API body. Malformed or missing -> None."""
    if not isinstance(payload, Mapping):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    try:
        in_details = usage.get("input_tokens_details") or {}
        out_details = usage.get("output_tokens_details") or {}
        output_items = payload.get("output") or []
        return Usage(
            input_tokens=usage["input_tokens"],
            cached_input_tokens=in_details.get("cached_tokens", 0) or 0,
            output_tokens=usage["output_tokens"],
            reasoning_tokens=out_details.get("reasoning_tokens", 0) or 0,
            web_search_calls=sum(
                1
                for it in output_items
                if isinstance(it, Mapping) and it.get("type") == "web_search_call"
            ),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def output_text(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if isinstance(block, Mapping) and block.get("type") == "output_text":
                parts.append(str(block.get("text", "")))
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class EmberResult:
    text: str
    status: str | None
    usage: Usage | None
    response_id: str | None
    record: CallRecord
    payload: Mapping[str, Any]


@dataclass
class EmberClient:
    """Budgeted Muse Ember client. Bound to one ledger stage for its lifetime."""

    ledger: Ledger
    stage: Stage
    transport: Transport
    model: str = EMBER_MODEL

    def __post_init__(self) -> None:
        self.stage = Stage(self.stage)

    @classmethod
    def live(cls, ledger: Ledger, stage: Stage | str, *, live: bool) -> EmberClient:
        return cls(ledger=ledger, stage=Stage(stage), transport=HttpxTransport(live=live))

    def respond(
        self,
        *,
        input: str | Sequence[Mapping[str, Any]],
        agent_role: str,
        input_kind: InputKind | str,
        reasoning_effort: str,
        max_output_tokens: int,
        instructions: str | None = None,
        text_format: Mapping[str, Any] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        est_input_tokens: int | None = None,
        item_id: str | None = None,
        prompt_version: str | None = None,
        prompt_sha256: str | None = None,
        prompt_cache_key: str | None = None,
    ) -> EmberResult:
        if reasoning_effort not in ALLOWED_EFFORTS:
            raise ValueError(
                f"reasoning_effort must be one of {sorted(ALLOWED_EFFORTS)}; Ember rejects 'none' "
                "and defaults to the most expensive level ('max') when omitted"
            )
        if max_output_tokens < MIN_MAX_OUTPUT_TOKENS:
            raise ValueError(f"max_output_tokens must be >= {MIN_MAX_OUTPUT_TOKENS}")
        for tool in tools or ():
            if tool.get("type") not in ALLOWED_TOOL_TYPES:
                raise ValueError(
                    f"tool type {tool.get('type')!r} refused: only client-executed function/custom "
                    "tools are allowed (hosted tools bill extra and bypass policy)"
                )

        texts, items, text_only = _collect_text(input)
        if instructions:
            texts.append(instructions)
        if prompt_cache_key:
            texts.append(prompt_cache_key)
        extra = [json.dumps(x) for x in (tools, text_format) if x]
        if est_input_tokens is None:
            if not text_only:
                raise ValueError(
                    "media input needs an explicit est_input_tokens upper bound "
                    "(measure it with POST /v1/responses/input_tokens first)"
                )
            est_input_tokens = estimate_text_input_tokens([*texts, *extra], items=items)

        body: dict[str, Any] = {
            "model": self.model,
            "input": input if isinstance(input, str) else list(input),
            "max_output_tokens": max_output_tokens,
            "reasoning": {"effort": reasoning_effort},
            "store": False,
        }
        if instructions:
            body["instructions"] = instructions
        if text_format:
            body["text"] = {"format": dict(text_format)}
        if tools:
            body["tools"] = [dict(t) for t in tools]
        if prompt_cache_key:
            body["prompt_cache_key"] = prompt_cache_key

        request = CallRequest(
            stage=self.stage,
            model=self.model,
            agent_role=agent_role,
            input_kind=InputKind(input_kind),
            reasoning_effort=reasoning_effort,
            est_input_tokens=est_input_tokens,
            max_output_tokens=max_output_tokens,
            item_id=item_id,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
        )
        reservation = self.ledger.reserve(request)  # raises BudgetExceeded: nothing is sent

        started = time.monotonic()
        settled = False

        def latency() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            try:
                resp = self.transport.post_json("/responses", body)
            except TransportError:
                self.ledger.settle(
                    reservation, outcome=Outcome.FAILED, usage=None, latency_ms=latency()
                )
                settled = True
                raise
            payload = resp.payload if isinstance(resp.payload, Mapping) else {}
            if resp.status_code >= 400:
                err = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
                outcome = Outcome.REJECTED if resp.status_code < 500 else Outcome.FAILED
                self.ledger.settle(reservation, outcome=outcome, usage=None, latency_ms=latency())
                settled = True
                raise EmberAPIError(resp.status_code, err.get("type"), err.get("code"))

            usage = parse_usage(payload)
            status = payload.get("status")
            if usage is None:
                outcome = Outcome.NO_USAGE
            elif status == "completed":
                outcome = Outcome.OK
            elif status == "incomplete":
                outcome = Outcome.INCOMPLETE
            else:
                outcome = Outcome.FAILED
            response_id = payload.get("id")
            if not (isinstance(response_id, str) and SAFE_ID_PATTERN.match(response_id)):
                response_id = None
            record = self.ledger.settle(
                reservation,
                outcome=outcome,
                usage=usage,
                latency_ms=latency(),
                api_response_id=response_id,
            )
            settled = True
            return EmberResult(
                text=output_text(payload),
                status=status if isinstance(status, str) else None,
                usage=usage,
                response_id=response_id,
                record=record,
                payload=payload,
            )
        finally:
            if not settled:  # unexpected exception: keep the full reservation as spent
                # LedgerStateError means the try block already settled (e.g. the DB commit
                # landed but the trace write failed): the money is counted, so let the
                # original error propagate instead of masking it.
                with contextlib.suppress(LedgerStateError):
                    self.ledger.settle(
                        reservation, outcome=Outcome.FAILED, usage=None, latency_ms=latency()
                    )

    def count_input_tokens(
        self,
        *,
        input: str | Sequence[Mapping[str, Any]],
        agent_role: str,
        instructions: str | None = None,
        text_format: Mapping[str, Any] | None = None,
        item_id: str | None = None,
    ) -> int:
        """Exact input-token count via POST /v1/responses/input_tokens (no generation).

        Used to bound media calls (PDF, image, audio), whose token cost depends on resolution
        and preprocessing. Each count is logged to the budget-events trace for audit.
        """
        body: dict[str, Any] = {
            "model": self.model,
            "input": input if isinstance(input, str) else list(input),
        }
        if instructions:
            body["instructions"] = instructions
        if text_format:
            body["text"] = {"format": dict(text_format)}
        resp = self.transport.post_json("/responses/input_tokens", body)
        payload = resp.payload if isinstance(resp.payload, Mapping) else {}
        if resp.status_code >= 400:
            err = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
            raise EmberAPIError(resp.status_code, err.get("type"), err.get("code"))
        count = payload.get("input_tokens")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise EmberAPIError(resp.status_code, "invalid_count_response", None)
        self.ledger.record_token_count(
            stage=self.stage, agent_role=agent_role, item_id=item_id, input_tokens=count
        )
        return count
