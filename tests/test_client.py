import json

import pytest

from conftest import FakeTransport, network_error, ok_response, responses_payload
from forgeember.budget.ledger import (
    BudgetConfig,
    Outcome,
    SecretInTraceError,
    StageLimitExceeded,
)
from forgeember.budget.pricing import usd_to_nusd
from forgeember.ember.client import (
    EmberAPIError,
    EmberClient,
    HttpxTransport,
    LiveCallsDisabled,
    TransportError,
    TransportResponse,
    estimate_text_input_tokens,
    output_text,
    parse_usage,
)


def client(ledger, *script, stage="dev"):
    t = FakeTransport(script=list(script))
    return EmberClient(ledger=ledger, stage=stage, transport=t), t


def call(c, **kw):
    args = {
        "input": "Classify: 'I was charged twice for my October invoice.'",
        "agent_role": "smoke",
        "input_kind": "text",
        "reasoning_effort": "minimal",
        "max_output_tokens": 64,
    }
    args.update(kw)
    return c.respond(**args)


def test_successful_call_reserves_sends_and_settles_actual_usage(ledger):
    c, t = client(ledger, ok_response(input_tokens=150, cached=0, output_tokens=40, reasoning=30))
    result = call(c, instructions="Answer in one word.", prompt_cache_key="smoke-v1")
    path, body = t.calls[0]
    assert path == "/responses"
    assert body["model"] == "muse-ember-1.0"
    assert body["reasoning"] == {"effort": "minimal"}
    assert body["store"] is False
    assert body["max_output_tokens"] == 64
    assert body["instructions"] == "Answer in one word."
    assert body["prompt_cache_key"] == "smoke-v1"
    assert result.text == "ok"
    assert result.response_id == "resp_abc123"
    assert result.record.outcome == Outcome.OK.value
    assert result.record.cost_nusd == 150 * 100 + 40 * 500
    assert result.record.stage == "dev"
    assert ledger.calls(status="open") == []


def test_budget_refusal_means_nothing_is_sent(make_ledger):
    tiny = BudgetConfig(
        grant_nusd=usd_to_nusd("10"), global_cap_nusd=10, stage_limits_nusd={"dev": 10}
    )
    ledger = make_ledger(tiny)
    c, t = client(ledger, ok_response())
    with pytest.raises(StageLimitExceeded):
        call(c)
    assert t.calls == []
    assert ledger.calls() == []


@pytest.mark.parametrize("effort", [None, "none", "MAX", "ultra"])
def test_reasoning_effort_must_be_explicit_and_valid(ledger, effort):
    c, t = client(ledger)
    with pytest.raises(ValueError, match="reasoning_effort"):
        call(c, reasoning_effort=effort)
    assert t.calls == []


def test_max_output_tokens_minimum(ledger):
    c, _ = client(ledger)
    with pytest.raises(ValueError):
        call(c, max_output_tokens=15)


def test_hosted_tools_refused(ledger):
    c, t = client(ledger)
    with pytest.raises(ValueError, match="web_search"):
        call(c, tools=[{"type": "web_search"}])
    assert t.calls == []


def test_function_tools_allowed_and_counted_in_estimate(ledger):
    tool = {"type": "function", "name": "lookup_vendor", "parameters": {"type": "object"}}
    c, t = client(ledger, ok_response())
    result = call(c, tools=[tool])
    assert t.calls[0][1]["tools"] == [tool]
    assert result.record.est_input_tokens >= len(json.dumps(tool)) + 512


def test_structured_output_goes_in_text_format(ledger):
    schema = {"type": "json_schema", "name": "route", "schema": {"type": "object"}, "strict": True}
    c, t = client(ledger, ok_response())
    call(c, text_format=schema)
    assert t.calls[0][1]["text"] == {"format": schema}


def test_media_input_requires_explicit_token_bound(ledger):
    media = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Extract the invoice fields."},
                {"type": "input_file", "file_data": "data:application/pdf;base64,AAAA"},
            ],
        }
    ]
    c, t = client(ledger, ok_response())
    with pytest.raises(ValueError, match="est_input_tokens"):
        call(c, input=media, input_kind="pdf")
    assert t.calls == []
    result = call(c, input=media, input_kind="pdf", est_input_tokens=5_000)
    assert result.record.est_input_tokens == 5_000


def test_transport_failure_keeps_full_reservation(ledger):
    c, _ = client(ledger, network_error())
    with pytest.raises(TransportError):
        call(c)
    [rec] = ledger.calls()
    assert rec.outcome == Outcome.FAILED.value
    assert rec.cost_nusd == rec.reservation_nusd


@pytest.mark.parametrize(
    ("status", "outcome"), [(400, Outcome.REJECTED), (429, Outcome.REJECTED), (503, Outcome.FAILED)]
)
def test_http_errors_keep_full_reservation(ledger, status, outcome):
    body = {"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded", "message": "slow"}}
    c, _ = client(ledger, TransportResponse(status_code=status, payload=body))
    with pytest.raises(EmberAPIError) as exc:
        call(c)
    assert exc.value.status_code == status
    assert "slow" not in str(exc.value)  # we never echo API message text
    [rec] = ledger.calls()
    assert rec.outcome == outcome.value
    assert rec.cost_nusd == rec.reservation_nusd


def test_missing_usage_keeps_full_reservation(ledger):
    payload = responses_payload()
    del payload["usage"]
    c, _ = client(ledger, TransportResponse(status_code=200, payload=payload))
    result = call(c)
    assert result.record.outcome == Outcome.NO_USAGE.value
    assert result.record.cost_nusd == result.record.reservation_nusd


def test_incomplete_response_settles_at_actual(ledger):
    c, _ = client(
        ledger,
        ok_response(
            status="incomplete", input_tokens=100, cached=0, output_tokens=64, reasoning=64
        ),
    )
    result = call(c)
    assert result.record.outcome == Outcome.INCOMPLETE.value
    assert result.record.cost_nusd == 100 * 100 + 64 * 500


def test_unexpected_exception_still_settles(ledger):
    class Boom(FakeTransport):
        def post_json(self, path, body):
            raise RuntimeError("bug")

    c = EmberClient(ledger=ledger, stage="dev", transport=Boom())
    with pytest.raises(RuntimeError):
        call(c)
    [rec] = ledger.calls()
    assert rec.status == "settled"
    assert rec.cost_nusd == rec.reservation_nusd


def test_suspicious_response_id_is_dropped(ledger):
    c, _ = client(ledger, ok_response(response_id="resp with spaces"))
    assert call(c).response_id is None


def test_live_transport_requires_flag_key_and_no_ci():
    with pytest.raises(LiveCallsDisabled, match="live=True"):
        HttpxTransport(live=False, environ={"MODEL_API_KEY": "x"})
    with pytest.raises(LiveCallsDisabled, match="CI"):
        HttpxTransport(live=True, environ={"CI": "true", "MODEL_API_KEY": "x"})
    with pytest.raises(LiveCallsDisabled, match="MODEL_API_KEY"):
        HttpxTransport(live=True, environ={})


def test_parse_usage_and_output_text():
    payload = responses_payload(input_tokens=1847, cached=1792, output_tokens=98, reasoning=0)
    payload["output"].append({"type": "web_search_call", "id": "ws_1"})
    u = parse_usage(payload)
    assert (u.input_tokens, u.cached_input_tokens, u.output_tokens, u.reasoning_tokens) == (
        1847,
        1792,
        98,
        0,
    )
    assert u.web_search_calls == 1
    assert output_text(payload) == "ok"
    assert parse_usage({"usage": {"input_tokens": "x", "output_tokens": 1}}) is None
    assert parse_usage({"usage": {"output_tokens": 1}}) is None
    assert parse_usage(None) is None
    assert output_text(None) == ""


def test_text_estimate_is_an_upper_bound_on_bytes():
    assert estimate_text_input_tokens(["héllo"]) == len("héllo".encode()) + 512 + 32


def test_prompt_cache_key_bytes_are_included_in_the_estimate(ledger):
    c, _ = client(ledger, ok_response(), ok_response())
    plain = call(c)
    keyed = call(c, prompt_cache_key="smoke-v1")
    assert keyed.record.est_input_tokens - plain.record.est_input_tokens == len(b"smoke-v1")


@pytest.mark.parametrize(
    "bad_input",
    [
        [42],
        ["a bare string is not an input item"],
        [{"role": "user", "content": {"type": "input_text"}}],
        [{"role": "user", "content": ["not a block"]}],
        [{"role": "user", "content": 42}],
    ],
)
def test_malformed_input_shapes_rejected_before_any_reservation(ledger, bad_input):
    c, t = client(ledger, ok_response())
    with pytest.raises(ValueError, match="input"):
        call(c, input=bad_input)
    assert t.calls == []
    assert ledger.calls() == []


def test_safety_net_settle_never_masks_the_original_error(ledger):
    c, _ = client(ledger, ok_response(response_id="LLM_000000000000_smoke"))
    with pytest.raises(SecretInTraceError):
        call(c)
    [rec] = ledger.calls()
    assert rec.status == "settled"  # the money stayed counted despite the trace refusal
