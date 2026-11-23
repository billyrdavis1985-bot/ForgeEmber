"""Muse Ember price table and exact cost arithmetic.

Source: https://dev.meta.ai/docs/pricing-rate-limits  (Muse Ember section)
Checked: 2026-11-23

All money is integer nano-dollars (1 USD = 1_000_000_000 nUSD). Every Ember price is an
exact integer number of nUSD per token at this scale, so ledger sums are exact:

    $0.10 / 1M tokens  = 100 nUSD per token
    $0.004 / 1M tokens =   4 nUSD per token
    $0.50 / 1M tokens  = 500 nUSD per token

Documented billing rules this module encodes (see docs/META_API_NOTES.md):

* The tier is set by the request's input tokens, cached tokens included. A request with more
  than 200K input tokens is billed at the long-context rates *in full*, not only past 200K.
  We treat "200K" as 200,000 tokens. If Meta means 204,800, we over-estimate slightly
  for requests between the two, which is the safe direction.
* ``cached_tokens`` is a subset of ``input_tokens``, billed at the cached rate. If a
  response ever reports more cached than input tokens, the subset invariant broke
  and the cheap rate can't be trusted, so the larger number bills at the full rate.
* ``reasoning_tokens`` is a subset of ``output_tokens``, billed at the output rate.
  Reasoning is never billed twice. If a response ever reports more reasoning
  tokens than output tokens, we bill the larger number (never under-count).
* Web search grounding costs $2.50 per 1,000 queries on top of tokens. ForgeEmber's client
  refuses hosted tools, but the price is kept here so a stray call is still costed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

NANO_PER_USD = 1_000_000_000

PRICING_SOURCE_URL = "https://dev.meta.ai/docs/pricing-rate-limits"
PRICING_CHECKED = "2026-11-23"


class PriceTierName(StrEnum):
    STANDARD = "standard"  # input tokens <= threshold
    LONG_CONTEXT = "long_context"  # input tokens > threshold


@dataclass(frozen=True, slots=True)
class PriceTier:
    """Per-token prices in integer nano-dollars."""

    name: PriceTierName
    input_nusd_per_token: int
    cached_input_nusd_per_token: int
    output_nusd_per_token: int


@dataclass(frozen=True, slots=True)
class ModelPricing:
    model: str
    standard: PriceTier
    long_context: PriceTier
    long_context_threshold_tokens: int
    web_search_nusd_per_query: int

    def tier_for(self, input_tokens: int) -> PriceTier:
        if input_tokens < 0:
            raise ValueError("input_tokens must be >= 0")
        if input_tokens > self.long_context_threshold_tokens:
            return self.long_context
        return self.standard


def usd_per_million_to_nusd_per_token(usd_per_million: str) -> int:
    """Convert a '$X per 1M tokens' price to exact integer nUSD per token."""
    value = Decimal(usd_per_million) * NANO_PER_USD / Decimal(1_000_000)
    if value != value.to_integral_value():
        raise ValueError(f"price {usd_per_million}/M is not an integer number of nUSD per token")
    return int(value)


MUSE_EMBER_1_0 = ModelPricing(
    model="muse-ember-1.0",
    standard=PriceTier(
        name=PriceTierName.STANDARD,
        input_nusd_per_token=usd_per_million_to_nusd_per_token("0.10"),
        cached_input_nusd_per_token=usd_per_million_to_nusd_per_token("0.004"),
        output_nusd_per_token=usd_per_million_to_nusd_per_token("0.50"),
    ),
    long_context=PriceTier(
        name=PriceTierName.LONG_CONTEXT,
        input_nusd_per_token=usd_per_million_to_nusd_per_token("0.30"),
        cached_input_nusd_per_token=usd_per_million_to_nusd_per_token("0.012"),
        output_nusd_per_token=usd_per_million_to_nusd_per_token("1.50"),
    ),
    long_context_threshold_tokens=200_000,
    web_search_nusd_per_query=2_500_000,  # $2.50 per 1,000 queries
)

PRICES: dict[str, ModelPricing] = {MUSE_EMBER_1_0.model: MUSE_EMBER_1_0}


class UnknownModelError(KeyError):
    """Raised for a model with no verified price. We never guess a price."""


def pricing_for(model: str) -> ModelPricing:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelError(
            f"no verified price for model {model!r}; add it to pricing.py with its source URL"
        ) from None


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    tier: PriceTierName
    billable_input_tokens: int
    uncached_input_tokens: int
    cached_input_tokens: int
    billable_output_tokens: int
    input_nusd: int
    cached_input_nusd: int
    output_nusd: int
    web_search_nusd: int

    @property
    def total_nusd(self) -> int:
        return self.input_nusd + self.cached_input_nusd + self.output_nusd + self.web_search_nusd


def _non_negative(**values: int) -> None:
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")


def actual_cost(
    model: str,
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
    web_search_calls: int = 0,
) -> CostBreakdown:
    """Cost of a completed call from the API's reported usage.

    Defensive rules (never under-count):
    * cached tokens above input tokens: the split can't be trusted, so the larger
      number is billed in full at the uncached rate.
    * reasoning tokens above output tokens: output is treated as the larger number.
    """
    _non_negative(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        web_search_calls=web_search_calls,
    )
    pricing = pricing_for(model)
    billable_input = max(input_tokens, cached_input_tokens)
    if cached_input_tokens > input_tokens:
        uncached, cached_billed = billable_input, 0
    else:
        uncached, cached_billed = input_tokens - cached_input_tokens, cached_input_tokens
    billable_output = max(output_tokens, reasoning_tokens)
    tier = pricing.tier_for(billable_input)
    return CostBreakdown(
        tier=tier.name,
        billable_input_tokens=billable_input,
        uncached_input_tokens=uncached,
        cached_input_tokens=cached_billed,
        billable_output_tokens=billable_output,
        input_nusd=uncached * tier.input_nusd_per_token,
        cached_input_nusd=cached_billed * tier.cached_input_nusd_per_token,
        output_nusd=billable_output * tier.output_nusd_per_token,
        web_search_nusd=web_search_calls * pricing.web_search_nusd_per_query,
    )


def worst_case_cost(model: str, *, input_tokens_upper_bound: int, max_output_tokens: int) -> int:
    """Upper bound on a call's cost before it is made.

    Assumes no cache hits, the tier implied by the input upper bound, and that the model
    uses its whole ``max_output_tokens`` budget (reasoning + visible output share it).
    """
    _non_negative(
        input_tokens_upper_bound=input_tokens_upper_bound, max_output_tokens=max_output_tokens
    )
    return actual_cost(
        model,
        input_tokens=input_tokens_upper_bound,
        cached_input_tokens=0,
        output_tokens=max_output_tokens,
    ).total_nusd


def cache_savings_nusd(model: str, *, input_tokens: int, cached_input_tokens: int) -> int:
    """What the cached tokens would have cost at the uncached rate, minus what they cost."""
    if cached_input_tokens > input_tokens:
        return 0  # malformed usage is billed at the full rate (actual_cost), so nothing was saved
    tier = pricing_for(model).tier_for(input_tokens)
    return cached_input_tokens * (tier.input_nusd_per_token - tier.cached_input_nusd_per_token)


def nusd_to_usd(nusd: int) -> Decimal:
    return Decimal(nusd) / NANO_PER_USD


def usd_to_nusd(usd: str | Decimal | int) -> int:
    value = Decimal(str(usd)) * NANO_PER_USD
    if value != value.to_integral_value():
        raise ValueError(f"{usd} USD is finer than 1 nano-dollar")
    return int(value)


def fmt_usd(nusd: int, *, places: int | None = None) -> str:
    """Human formatting: whole cents print as $X.YZ; sub-cent amounts keep enough precision."""
    usd = nusd_to_usd(nusd)
    if places is None:
        if nusd % (NANO_PER_USD // 100) == 0:
            places = 2
        elif abs(usd) < Decimal("0.0001"):
            places = 6
        else:
            places = 4
    sign = "-" if usd < 0 else ""
    return f"{sign}${abs(usd):.{places}f}"
