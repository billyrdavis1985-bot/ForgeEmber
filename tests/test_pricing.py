from decimal import Decimal

import pytest

from forgeember.budget.pricing import (
    MUSE_EMBER_1_0,
    PriceTierName,
    UnknownModelError,
    actual_cost,
    cache_savings_nusd,
    fmt_usd,
    nusd_to_usd,
    usd_per_million_to_nusd_per_token,
    usd_to_nusd,
    worst_case_cost,
)

M = "muse-ember-1.0"


def test_price_table_matches_meta_docs_exactly():
    s, lc = MUSE_EMBER_1_0.standard, MUSE_EMBER_1_0.long_context
    # $/1M -> nUSD/token: 0.10->100, 0.004->4, 0.50->500; 0.30->300, 0.012->12, 1.50->1500
    assert (s.input_nusd_per_token, s.cached_input_nusd_per_token, s.output_nusd_per_token) == (
        100,
        4,
        500,
    )
    assert (lc.input_nusd_per_token, lc.cached_input_nusd_per_token, lc.output_nusd_per_token) == (
        300,
        12,
        1500,
    )
    assert MUSE_EMBER_1_0.long_context_threshold_tokens == 200_000


def test_price_conversion_rejects_sub_nano_prices():
    with pytest.raises(ValueError):
        usd_per_million_to_nusd_per_token("0.0000001")


@pytest.mark.parametrize(
    ("input_tokens", "tier"),
    [
        (0, PriceTierName.STANDARD),
        (200_000, PriceTierName.STANDARD),
        (200_001, PriceTierName.LONG_CONTEXT),
        (1_000_000, PriceTierName.LONG_CONTEXT),
    ],
)
def test_tier_boundary(input_tokens, tier):
    assert MUSE_EMBER_1_0.tier_for(input_tokens).name is tier


def test_standard_cost_one_million_each():
    c = actual_cost(M, input_tokens=1_000_000 - 1, cached_input_tokens=0, output_tokens=0)
    assert c.tier is PriceTierName.LONG_CONTEXT  # 999,999 input tokens is over 200K
    c = actual_cost(M, input_tokens=100_000, cached_input_tokens=0, output_tokens=1_000_000)
    assert nusd_to_usd(c.total_nusd) == Decimal("0.01") + Decimal("0.50")


def test_long_context_bills_whole_request_at_long_rates():
    c = actual_cost(M, input_tokens=200_001, cached_input_tokens=0, output_tokens=1000)
    assert c.input_nusd == 200_001 * 300
    assert c.output_nusd == 1000 * 1500


def test_cached_tokens_are_a_subset_of_input_and_billed_at_cached_rate():
    c = actual_cost(M, input_tokens=1847, cached_input_tokens=1792, output_tokens=98)
    assert c.uncached_input_tokens == 55
    assert c.input_nusd == 55 * 100
    assert c.cached_input_nusd == 1792 * 4
    assert c.output_nusd == 98 * 500
    assert c.total_nusd == 55 * 100 + 1792 * 4 + 98 * 500


def test_cached_tokens_count_toward_the_tier():
    c = actual_cost(M, input_tokens=250_000, cached_input_tokens=240_000, output_tokens=0)
    assert c.tier is PriceTierName.LONG_CONTEXT
    assert c.cached_input_nusd == 240_000 * 12


def test_reasoning_tokens_are_billed_as_output_not_twice():
    c = actual_cost(
        M, input_tokens=100, cached_input_tokens=0, output_tokens=300, reasoning_tokens=250
    )
    assert c.billable_output_tokens == 300
    assert c.output_nusd == 300 * 500


def test_reasoning_above_output_is_never_undercounted():
    # If the API ever reported reasoning outside output_tokens, bill the larger figure.
    c = actual_cost(
        M, input_tokens=100, cached_input_tokens=0, output_tokens=40, reasoning_tokens=900
    )
    assert c.billable_output_tokens == 900


def test_cached_above_input_is_never_undercounted():
    c = actual_cost(M, input_tokens=100, cached_input_tokens=150, output_tokens=0)
    assert c.billable_input_tokens == 150
    assert c.uncached_input_tokens == 0


def test_web_search_fee():
    c = actual_cost(M, input_tokens=0, cached_input_tokens=0, output_tokens=0, web_search_calls=2)
    assert c.total_nusd == 2 * 2_500_000  # $2.50 per 1,000 queries


def test_worst_case_assumes_no_cache_and_full_output():
    wc = worst_case_cost(M, input_tokens_upper_bound=10_000, max_output_tokens=2_000)
    assert wc == 10_000 * 100 + 2_000 * 500
    assert (
        wc
        >= actual_cost(
            M,
            input_tokens=9_000,
            cached_input_tokens=8_000,
            output_tokens=2_000,
            reasoning_tokens=1_500,
        ).total_nusd
    )


def test_worst_case_uses_long_tier_when_bound_exceeds_threshold():
    wc = worst_case_cost(M, input_tokens_upper_bound=300_000, max_output_tokens=100)
    assert wc == 300_000 * 300 + 100 * 1500


def test_negative_tokens_rejected():
    with pytest.raises(ValueError):
        actual_cost(M, input_tokens=-1, cached_input_tokens=0, output_tokens=0)


def test_unknown_model_refused():
    with pytest.raises(UnknownModelError):
        actual_cost("muse-spark-1.3", input_tokens=1, cached_input_tokens=0, output_tokens=1)


def test_cache_savings():
    assert cache_savings_nusd(M, input_tokens=2000, cached_input_tokens=1000) == 1000 * (100 - 4)


@pytest.mark.parametrize(
    ("nusd", "text"),
    [
        (0, "$0.00"),
        (47_000, "$0.000047"),
        (1_234_500, "$0.0012"),
        (9 * 10**9, "$9.00"),
        (750_000_000, "$0.75"),
        (1_234_567_890, "$1.2346"),
        (-10_000_000, "-$0.01"),
    ],
)
def test_fmt_usd(nusd, text):
    assert fmt_usd(nusd) == text


def test_usd_round_trip():
    assert usd_to_nusd("9.00") == 9_000_000_000
    with pytest.raises(ValueError):
        usd_to_nusd("0.0000000001")
