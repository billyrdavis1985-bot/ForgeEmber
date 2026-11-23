# Budget design

Going over $10 of Meta credit disqualifies us, and Meta has no hard spend cap. So ForgeEmber's own code is the cap. The budget module (`src/forgeember/budget/`) was built and tested before any live call existed.

## Limits

| | |
|---|---|
| Grant | $10.00 |
| **Global hard cap** | **$9.00** (the $1.00 margin absorbs estimation error, and any usage our ledger can't see) |
| Stages | dev $1.50 · eval $1.50 · scale $4.00 · demo $0.75 · reserve $1.25 = $9.00 |

A stage stops at its limit. `forgeember budget transfer --from-stage A --to-stage B --usd X --approved-by "Billy Davis" --reason "..."` moves unspent headroom. It requires a named approver and a reason, writes a line to `traces/budget-events.jsonl`, and can never raise the global cap.

## Prices (`budget/pricing.py`)

Source: https://dev.meta.ai/docs/pricing-rate-limits, checked 2026-11-23.

| Prompt size (input tokens, cached included) | Input | Cached input | Output |
|---|---|---|---|
| ≤ 200,000 | $0.10/M | $0.004/M | $0.50/M |
| > 200,000 (whole request) | $0.30/M | $0.012/M | $1.50/M |

All money is **integer nano-dollars** (1 USD = 10⁹ nUSD). Every Ember price is an exact integer per token at that scale: 100, 4 and 500 nUSD; 300, 12 and 1,500 nUSD. Sums are exact, with no float drift.

## Reserve, then settle

1. **Estimate the worst case before the call:** input-token upper bound × input price + `max_output_tokens` × output price, at the tier the upper bound implies, with zero cache hits assumed.
   - Text: UTF-8 bytes of all text (instructions, input, prompt cache key, tool and schema JSON) + 512 + 32 per input item. A BPE token covers at least one byte.
   - Media: the caller must pass an explicit upper bound, measured with `POST /v1/responses/input_tokens` (Phase 1). The client refuses media input without one.
2. **Refuse** if `committed(stage) + reservation > stage limit`, or `committed(all) + reservation > $9.00`, where committed = settled cost + open reservations. The check and the insert run in one SQLite `BEGIN IMMEDIATE` transaction, so they're atomic across threads and processes. A refused call sends nothing.
3. **Settle after the call** from the API's usage:
   `cost = (input − cached) × input + cached × cached_rate + max(output, reasoning) × output (+ web search fees)`.
   Reasoning tokens are inside `output_tokens` per Meta's API reference. The `max` never bills them twice, and never under-counts if a response reports otherwise.
4. **Missing usage or a failed call keeps the full reservation as spent**: transport error, timeout, 4xx, 5xx, a 2xx without usage, or any unexpected exception. If a failed call does report usage, it settles at max(reservation, actual).
5. **Overruns are never trimmed.** If actual cost exceeds the reservation, the actual is recorded and `overrun` is flagged. The global cap is the backstop.
6. **Crash recovery:** a reservation left open by a crash still counts as committed. `forgeember budget expire-open` closes old ones as `orphaned`, at the full reservation.

## Ledger

- **SQLite (WAL, `synchronous=FULL`)** at `var/ledger.sqlite`. This is the local source of truth and is gitignored.
- **An append-only JSONL trace** at `traces/calls.jsonl`, one line per settled call, committed:
  `ts, call_id, stage, agent_role, model, reasoning_effort, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, cost_usd, reservation_usd, prompt_version, prompt_sha256, input_kind, item_id, outcome, latency_ms, api_response_id`, plus the exact `cost_nusd` and `reservation_nusd`, `price_tier`, `overrun`, `reserved_at`, `est_input_tokens`, `max_output_tokens` and `web_search_calls`.
- **Thread-safe:** one connection guarded by a lock, plus `BEGIN IMMEDIATE` for other processes. JSONL appends take an `flock`.
- **No secrets, ever.** Every line is checked against credential patterns (`LLM_<digits>_…`, `Bearer …`, `Authorization:`, `sk-…`) before it's written. A match refuses the write. The API key is never passed to the ledger at all.

## Reconciliation

`forgeember budget reconcile --meta-usd X [--meta-input-tokens N --meta-output-tokens N] --period-start 2026-11-23 --period-end 2026-12-02 --note "..."` records Meta's Usage-page total next to the ledger total. It exits with code 2 and a warning if **ledger < Meta**. Likely causes: Muse Code or Playground usage on the same account (which our ledger can't see), or a pricing error. The ledger must never come in under Meta's bill.

## Reports

- `forgeember budget` shows: total vs the $10 grant, headroom to the $9.00 cap, spend by stage, input kind and agent role, cost per item, cost per 1,000 items, cache hit rate and savings, and reasoning-token share. Add `--json` for the snapshot.
- `scripts/render_budget_readme.py` writes `traces/budget-snapshot.json`, the README block between `<!-- budget:start -->` and `<!-- budget:end -->`, and `docs/assets/budget-chart.svg`. It first checks that the local ledger and the committed trace agree, so a fresh clone can't publish zeros over real numbers.
- **CI checks:** the README block equals the render of the snapshot, the chart equals the render of the snapshot, and the snapshot equals a recomputation from `traces/*.jsonl`. Any drift fails the build.
- `scripts/export_trace.py` writes `traces/submission-trace.jsonl` and `traces/summary.json` for the submission.

## Operator runbook

```bash
read -s MODEL_API_KEY && export MODEL_API_KEY       # never echoed, never saved
uv run python scripts/smoke_test.py --live --stage dev
uv run forgeember budget                            # check spend
uv run python scripts/render_budget_readme.py       # refresh README numbers, then commit traces/ + README
uv run forgeember budget reconcile --meta-usd <Usage page total> --note "Usage page <date>"
```
