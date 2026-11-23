import json
import threading
from datetime import timedelta

import pytest

from conftest import req
from forgeember.budget.ledger import (
    DEFAULT_CONFIG,
    TRACE_SCHEMA,
    BudgetConfig,
    GlobalCapExceeded,
    LedgerStateError,
    Outcome,
    SecretInTraceError,
    Stage,
    StageLimitExceeded,
    Usage,
    assert_no_secrets,
    read_events,
    read_trace,
)
from forgeember.budget.pricing import usd_to_nusd, worst_case_cost

REQUIRED_TRACE_FIELDS = {
    "ts",
    "call_id",
    "stage",
    "agent_role",
    "model",
    "reasoning_effort",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_usd",
    "reservation_usd",
    "prompt_version",
    "prompt_sha256",
    "input_kind",
    "item_id",
    "outcome",
    "latency_ms",
    "api_response_id",
}

USAGE = Usage(input_tokens=1200, cached_input_tokens=1000, output_tokens=300, reasoning_tokens=120)
USAGE_COST = 200 * 100 + 1000 * 4 + 300 * 500


def small_config(**stages: str) -> BudgetConfig:
    limits = {k: usd_to_nusd(v) for k, v in stages.items()}
    return BudgetConfig(
        grant_nusd=usd_to_nusd("10.00"),
        global_cap_nusd=sum(limits.values()) or 1,
        stage_limits_nusd=limits,
    )


# ---------------------------------------------------------------- configuration
def test_default_config_matches_plan():
    assert DEFAULT_CONFIG.grant_nusd == usd_to_nusd("10.00")
    assert DEFAULT_CONFIG.global_cap_nusd == usd_to_nusd("9.00")
    assert DEFAULT_CONFIG.stage_limits_nusd == {
        "dev": usd_to_nusd("1.50"),
        "eval": usd_to_nusd("1.50"),
        "scale": usd_to_nusd("4.00"),
        "demo": usd_to_nusd("0.75"),
        "reserve": usd_to_nusd("1.25"),
    }
    assert sum(DEFAULT_CONFIG.stage_limits_nusd.values()) == DEFAULT_CONFIG.global_cap_nusd


@pytest.mark.parametrize(
    "kwargs",
    [
        {"grant_nusd": 10, "global_cap_nusd": 11, "stage_limits_nusd": {}},
        {"grant_nusd": 10, "global_cap_nusd": 5, "stage_limits_nusd": {"dev": 6}},
        {"grant_nusd": 10, "global_cap_nusd": 5, "stage_limits_nusd": {"prod": 1}},
        {"grant_nusd": 10, "global_cap_nusd": 5, "stage_limits_nusd": {"dev": -1}},
    ],
)
def test_bad_configs_rejected(kwargs):
    with pytest.raises(ValueError):
        BudgetConfig(**kwargs)


# ---------------------------------------------------------------- reserve / settle
def test_reserve_then_settle_records_actual_cost(ledger):
    r = ledger.reserve(req())
    assert r.reservation_nusd == worst_case_cost(
        "muse-ember-1.0", input_tokens_upper_bound=10_000, max_output_tokens=2_000
    )
    [open_call] = ledger.calls(status="open")
    assert open_call.call_id == r.call_id

    rec = ledger.settle(
        r, outcome=Outcome.OK, usage=USAGE, latency_ms=812, api_response_id="resp_1"
    )
    assert rec.status == "settled"
    assert rec.cost_nusd == USAGE_COST
    assert rec.overrun is False
    assert rec.price_tier == "standard"
    assert ledger.calls(status="open") == []


def test_open_reservations_count_against_the_stage_limit(make_ledger):
    ledger = make_ledger(small_config(dev="0.01"))
    # worst case: 10,000 in * 100 + 2,000 out * 500 = 2,000,000 nUSD = $0.002
    for _ in range(5):
        ledger.reserve(req())
    with pytest.raises(StageLimitExceeded) as exc:
        ledger.reserve(req())
    assert exc.value.committed_nusd == 5 * 2_000_000
    assert exc.value.scope == "stage:dev"


def test_refusal_happens_before_any_row_is_written(make_ledger):
    ledger = make_ledger(small_config(dev="0.001"))
    with pytest.raises(StageLimitExceeded):
        ledger.reserve(req())
    assert ledger.calls() == []


def test_settling_frees_the_unused_part_of_a_reservation(make_ledger):
    ledger = make_ledger(small_config(dev="0.004"))
    a = ledger.reserve(req())
    b = ledger.reserve(req())
    with pytest.raises(StageLimitExceeded):
        ledger.reserve(req())
    ledger.settle(a, outcome=Outcome.OK, usage=USAGE)
    ledger.settle(b, outcome=Outcome.OK, usage=USAGE)
    ledger.reserve(req())  # fits now that actual cost replaced worst case


def test_stage_limits_are_independent(make_ledger):
    ledger = make_ledger(small_config(dev="0.002", eval="0.002"))
    ledger.reserve(req(stage="dev"))
    with pytest.raises(StageLimitExceeded):
        ledger.reserve(req(stage="dev"))
    ledger.reserve(req(stage="eval"))


def test_global_cap_refuses_even_when_the_stage_has_room(make_ledger):
    # Overruns can push total spend past the sum of what stages reserved. The global
    # cap is the backstop.
    ledger = make_ledger(small_config(dev="0.005", eval="0.005"))
    r = ledger.reserve(req(stage="dev"))  # reserves $0.002
    huge = Usage(
        input_tokens=10_000, cached_input_tokens=0, output_tokens=16_000, reasoning_tokens=0
    )
    rec = ledger.settle(r, outcome=Outcome.OK, usage=huge)  # actual $0.009: an overrun
    assert rec.overrun is True
    assert rec.cost_nusd == 10_000 * 100 + 16_000 * 500
    with pytest.raises(GlobalCapExceeded):
        ledger.reserve(req(stage="eval"))  # eval has $0.005 room, global has $0.001


def test_stage_with_no_budget_refuses(make_ledger):
    ledger = make_ledger(small_config(dev="0.01"))
    with pytest.raises(StageLimitExceeded):
        ledger.reserve(req(stage="scale"))


@pytest.mark.parametrize(
    "outcome", [Outcome.FAILED, Outcome.REJECTED, Outcome.NO_USAGE, Outcome.OK]
)
def test_missing_usage_keeps_the_full_reservation(ledger, outcome):
    r = ledger.reserve(req())
    rec = ledger.settle(r, outcome=outcome, usage=None)
    assert rec.cost_nusd == r.reservation_nusd
    assert rec.input_tokens is None


def test_failed_call_with_partial_usage_keeps_at_least_the_reservation(ledger):
    r = ledger.reserve(req())
    rec = ledger.settle(r, outcome=Outcome.FAILED, usage=USAGE)
    assert rec.cost_nusd == max(r.reservation_nusd, USAGE_COST) == r.reservation_nusd


def test_incomplete_with_usage_settles_at_actual(ledger):
    r = ledger.reserve(req())
    rec = ledger.settle(r, outcome=Outcome.INCOMPLETE, usage=USAGE)
    assert rec.cost_nusd == USAGE_COST


def test_reasoning_tokens_counted_as_output_in_settlement(ledger):
    r = ledger.reserve(req())
    u = Usage(input_tokens=100, cached_input_tokens=0, output_tokens=50, reasoning_tokens=800)
    rec = ledger.settle(r, outcome=Outcome.OK, usage=u)
    assert rec.cost_nusd == 100 * 100 + 800 * 500  # never below the reasoning count


def test_double_settle_and_unknown_call_rejected(ledger):
    r = ledger.reserve(req())
    ledger.settle(r, outcome=Outcome.OK, usage=USAGE)
    with pytest.raises(LedgerStateError):
        ledger.settle(r, outcome=Outcome.OK, usage=USAGE)
    with pytest.raises(LedgerStateError):
        ledger.settle("nope", outcome=Outcome.OK, usage=USAGE)
    with pytest.raises(LedgerStateError):
        ledger.settle(ledger.reserve(req()), outcome=Outcome.ORPHANED, usage=None)


def test_expire_open_keeps_orphans_as_spent(ledger, clock):
    r = ledger.reserve(req())
    assert ledger.expire_open(timedelta(minutes=60)) == []
    clock.advance(timedelta(hours=2))
    [rec] = ledger.expire_open(timedelta(minutes=60))
    assert rec.outcome == Outcome.ORPHANED.value
    assert rec.cost_nusd == r.reservation_nusd
    assert [t.outcome for t in read_trace(ledger.trace_path)] == ["orphaned"]


def test_ledger_survives_reopen(make_ledger, tmp_path, clock):
    from forgeember.budget.ledger import Ledger

    first = make_ledger()
    r = first.reserve(req())
    first.settle(r, outcome=Outcome.OK, usage=USAGE)
    first.close()
    again = Ledger(db_path=first.db_path, trace_path=first.trace_path, clock=clock)
    try:
        assert [c.cost_nusd for c in again.calls()] == [USAGE_COST]
    finally:
        again.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"agent_role": "Bad Role"},
        {"reasoning_effort": "MAX!"},
        {"prompt_sha256": "xyz"},
        {"item_id": "has space"},
        {"max_output_tokens": 0},
        {"stage": "prod"},
        {"input_kind": "fax"},
        {"model": "gpt-x"},
    ],
)
def test_invalid_requests_rejected(bad):
    with pytest.raises((ValueError, KeyError)):
        req(**bad)


def test_usage_validation():
    with pytest.raises(ValueError):
        Usage(input_tokens=-1, cached_input_tokens=0, output_tokens=0, reasoning_tokens=0)
    with pytest.raises(ValueError):
        Usage(input_tokens=True, cached_input_tokens=0, output_tokens=0, reasoning_tokens=0)


# ---------------------------------------------------------------- trace
def test_trace_has_one_line_per_settled_call_with_required_fields(ledger):
    for _ in range(3):
        ledger.settle(
            ledger.reserve(req()), outcome=Outcome.OK, usage=USAGE, api_response_id="resp_x"
        )
    ledger.reserve(req())  # open: not traced yet
    lines = ledger.trace_path.read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        data = json.loads(line)
        assert data.keys() >= REQUIRED_TRACE_FIELDS
        assert data["schema"] == TRACE_SCHEMA
        assert data["cost_nusd"] == USAGE_COST
        assert data["cost_usd"] == pytest.approx(USAGE_COST / 1e9)
    assert {r.call_id for r in read_trace(ledger.trace_path)} == {
        c.call_id for c in ledger.calls(status="settled")
    }


def test_trace_is_append_only_across_reopen(make_ledger, clock):
    from forgeember.budget.ledger import Ledger

    first = make_ledger()
    first.settle(first.reserve(req()), outcome=Outcome.OK, usage=USAGE)
    first.close()
    again = Ledger(db_path=first.db_path, trace_path=first.trace_path, clock=clock)
    try:
        again.settle(again.reserve(req()), outcome=Outcome.OK, usage=USAGE)
    finally:
        again.close()
    assert len(read_trace(first.trace_path)) == 2


def test_trace_round_trips_records(ledger):
    rec = ledger.settle(ledger.reserve(req()), outcome=Outcome.OK, usage=USAGE, latency_ms=5)
    [back] = read_trace(ledger.trace_path)
    assert back == rec


def test_duplicate_trace_lines_detected(ledger):
    ledger.settle(ledger.reserve(req()), outcome=Outcome.OK, usage=USAGE)
    line = ledger.trace_path.read_text()
    ledger.trace_path.write_text(line + line)
    with pytest.raises(ValueError, match="duplicate"):
        read_trace(ledger.trace_path)


@pytest.mark.parametrize(
    "text",
    [
        "LLM_000000000000_FAKE_TEST_VALUE",
        "Bearer abcdefghijklmnop",
        "Authorization: x",
        "sk-abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_secret_patterns_are_refused(text):
    with pytest.raises(SecretInTraceError):
        assert_no_secrets(json.dumps({"note": text}))


def test_secret_never_written_to_events(ledger):
    with pytest.raises(SecretInTraceError):
        ledger.reconcile(meta_total_nusd=0, note="key LLM_000000000000_FAKE_TEST_VALUE")
    assert not ledger.events_path.exists() or "LLM_" not in ledger.events_path.read_text()


def test_api_response_id_with_odd_characters_rejected(ledger):
    with pytest.raises(ValueError):
        ledger.settle(ledger.reserve(req()), outcome=Outcome.OK, usage=USAGE, api_response_id="a b")


# ---------------------------------------------------------------- thread safety
def test_concurrent_reservations_never_exceed_the_limit(make_ledger):
    ledger = make_ledger(small_config(dev="0.02"))  # room for exactly 10 x $0.002
    wins, losses = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(40)

    def worker():
        barrier.wait()
        try:
            r = ledger.reserve(req())
        except StageLimitExceeded:
            with lock:
                losses.append(1)
            return
        with lock:
            wins.append(r)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 10
    assert len(losses) == 30


def test_concurrent_settlements_sum_exactly(make_ledger):
    ledger = make_ledger()
    reservations = [ledger.reserve(req(stage="scale")) for _ in range(60)]
    threads = [
        threading.Thread(
            target=ledger.settle, args=(r,), kwargs={"outcome": Outcome.OK, "usage": USAGE}
        )
        for r in reservations
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    settled = ledger.calls(status="settled")
    assert len(settled) == 60
    assert sum(c.cost_nusd for c in settled) == 60 * USAGE_COST
    assert len(read_trace(ledger.trace_path)) == 60


def test_two_ledger_handles_on_one_db_share_limits(make_ledger, clock):
    """Two connections (as two processes would have) cannot jointly overspend a stage."""
    from forgeember.budget.ledger import Ledger

    a = make_ledger(small_config(dev="0.004"))
    b = Ledger(db_path=a.db_path, trace_path=a.trace_path, config=a.config, clock=clock)
    try:
        a.reserve(req())
        b.reserve(req())
        with pytest.raises(StageLimitExceeded):
            a.reserve(req())
        with pytest.raises(StageLimitExceeded):
            b.reserve(req())
    finally:
        b.close()


# ---------------------------------------------------------------- transfers
def test_transfer_requires_approval_and_moves_headroom(ledger):
    with pytest.raises(ValueError):
        ledger.transfer(
            from_stage=Stage.RESERVE,
            to_stage=Stage.SCALE,
            amount_nusd=1,
            approved_by=" ",
            reason="x",
        )
    t = ledger.transfer(
        from_stage=Stage.RESERVE,
        to_stage=Stage.SCALE,
        amount_nusd=usd_to_nusd("0.50"),
        approved_by="Billy Davis",
        reason="Gate B: scale run needs 10k items",
    )
    limits = ledger.effective_stage_limits()
    assert limits["scale"] == usd_to_nusd("4.50")
    assert limits["reserve"] == usd_to_nusd("0.75")
    assert sum(limits.values()) == DEFAULT_CONFIG.global_cap_nusd  # cap never moves
    transfers, _ = read_events(ledger.events_path)
    assert transfers == [t]


def test_transfer_cannot_exceed_source_headroom_or_be_self(ledger):
    with pytest.raises(StageLimitExceeded):
        ledger.transfer(
            from_stage=Stage.DEMO,
            to_stage=Stage.SCALE,
            amount_nusd=usd_to_nusd("0.76"),
            approved_by="Billy Davis",
            reason="too much",
        )
    with pytest.raises(ValueError):
        ledger.transfer(
            from_stage=Stage.DEMO, to_stage=Stage.DEMO, amount_nusd=1, approved_by="B", reason="r"
        )
    with pytest.raises(ValueError):
        ledger.transfer(
            from_stage=Stage.DEMO, to_stage=Stage.DEV, amount_nusd=0, approved_by="B", reason="r"
        )


# ---------------------------------------------------------------- reconciliation
def test_reconcile_flags_undercount(ledger):
    ledger.settle(ledger.reserve(req()), outcome=Outcome.OK, usage=USAGE)
    ok = ledger.reconcile(meta_total_nusd=USAGE_COST - 1, note="Usage page 11/23")
    assert ok.difference_nusd == 1 and not ok.undercounts
    bad = ledger.reconcile(meta_total_nusd=USAGE_COST + 5_000, meta_input_tokens=1200)
    assert bad.undercounts
    assert bad.ledger_input_tokens == 1200
    _, recs = read_events(ledger.events_path)
    assert [r.difference_nusd for r in recs] == [1, -5_000]
    assert ledger.reconciliations()[-1] == bad


def test_reconcile_counts_open_reservations_as_spent(ledger):
    r = ledger.reserve(req())
    rec = ledger.reconcile(meta_total_nusd=0)
    assert rec.ledger_total_nusd == r.reservation_nusd
    with pytest.raises(ValueError):
        ledger.reconcile(meta_total_nusd=-1)
