# ForgeEmber

**ForgeEmber screens every email, invoice, attachment and voicemail a small business receives, for a fraction of a cent each.** It turns messy input into verified, routed actions, and holds anything risky (payments, bank-detail changes, outside sends) for a person.

> **Ember proposes, and code decides.** Muse Ember extracts, classifies, investigates and proposes. Deterministic code checks the evidence, applies policy, enforces the budget and executes. Only `policy.py` can approve an action, and Ember can never execute a payment, a bank-detail change, an outside send or a deletion.

## Submission overview

| | |
|---|---|
| **What it is** | A screening pipeline for a small business's inbound mail, documents and voicemail. It extracts fields, detects invoice fraud and bank-detail-change scams, routes the rest, and keeps a full receipt of every decision and every cent spent. |
| **Who it's for** | Small businesses and solo founders with no security team. They get hit hardest by invoice fraud, bank-detail-change scams and impersonation. |
| **Problem statements** | (1) *Cheap enough to run on everything*: the same task over thousands of real items, end to end. (2) *Beyond text, under budget*: emails, PDFs, scans, photos and voicemails in, verified actions out. |
| **Prizes we're aiming for** | Best Frontier Build · Scale the Real World |
| **Model** | Muse Ember (`muse-ember-1.0`) through the Meta Model API, for every agent |
| **Headline result** | **Screened 6,458 emails for $1.75 in model calls ($0.27 per 1,000): 96.6% recall on 3,458 real phishing emails, 99.7% precision. Read the totals on 998 real receipt photos straight from the image: 99.0% correct against human labels, $0.76 for the run.** See Results below. |
| **Demo video** | _Recording Dec 1. Link goes here._ |
| **Built by** | Built solo by Billy Davis during Nov 23–Dec 2, 2026, with Muse Code (Muse Spark) and Claude as AI coding assistants; no pre-existing code. |

## Meta tooling, and the job each piece does

| Capability | Its job in ForgeEmber | Status |
|---|---|---|
| Responses API on `muse-ember-1.0` | Every agent call, stateless (`store: false`), through one budgeted client | Client built and tested with fakes; first live call pending review |
| Reasoning-effort levels | Low effort for bulk screening, higher only for escalations; cost saved is reported | Effort is required on every call (Ember defaults to `max` otherwise) |
| Structured output (`text.format` JSON schema) | Strict outputs, validated again by Pydantic | Built: four strict schemas, each finding with a verbatim quote that code checks |
| Native PDF and image input | Scanned invoices and receipts straight to structured fields, with no OCR library | Built and run at scale: 998 real receipt photos (CORD), 99.0% of totals correct, $0.76 per 1,000 photos |
| Native audio input | Voicemails straight to actions | Input path built; voicemail fixtures pending |
| Tool calling | Read-only lookups; proposed actions are function calls that policy intercepts | Planned for the identity specialist; code supplies baseline facts today (hosted tools are refused) |
| Prompt caching (cached input $0.004/M) | Shared instructions and few-shot examples cached; hit rate and dollars saved reported | Stable instructions first, `prompt_cache_key` per prompt version; hit rate measured in the ledger |
| 1M-token context | A vendor's baseline built from its whole email history in one call | Phase 2 |
| Multi-agent on Ember | Coordinator, specialists and a skeptic, all on Ember | Built: coordinator, document and risk specialists, skeptic on escalations |
| Video input (stretch) | A screen recording of a bank-detail change in a payment portal gets flagged | Gate D (Nov 30) |
| Muse Code | Muse Spark coding sessions; trajectory exported with `/export trajectory` | In use |

## Architecture

```
INPUTS              ENVELOPE               EMBER AGENTS (muse-ember-1.0)           CODE (authority)            OUTPUTS
.eml email    -+    id, sha256,     ->     Coordinator: plans, dispatches,  ->     Verifier: schema, quotes   ->  Invoice/payment HOLD queue
PDF / scan     |    source, mime,          picks reasoning effort                  exist in source, totals         Research notes (stretch)
image          |    received_at            Specialists: identity, document,        add up, baseline match         Task list
audio (vm)     |                           payment-change                          Policy: tiers T0/T1/T2         Receipts ledger + trace
video (stretch)-+                           Skeptic: argues the opposite            Budget: reserve, cap, settle
                                           Read-only tools: lookup_vendor,
                                           lookup_sender_history, search_notes
```

Policy tiers: **T0** automatic (file, tag, route a note) · **T1** notify the operator · **T2** hold for human approval (payments, bank-detail changes, outside sends, deletions). Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quickstart (no API key needed)

```bash
uv sync
uv run pytest                 # full test suite: fakes only, no network
uv run forgeember budget      # budget report from the local ledger
uv run python scripts/gen_fixtures.py   # regenerate the 13 fictional, labeled fixtures
```

Screening a real item needs a key, `--live` and a budget stage:

```bash
read -s MODEL_API_KEY && export MODEL_API_KEY
uv run forgeember screen fixtures/emails/em-001-bank-change-lookalike.eml --stage dev --live
uv run forgeember holds        # payments and bank-detail changes waiting for a person
uv run forgeember console      # approval console at http://127.0.0.1:8000 (approve/reject holds)
uv run forgeember eval --stage eval --live   # score all 13 labeled fixtures (Gate A)
```

Scale run: build the corpora (free), then screen them in bulk mode, resumable and capped by the `scale` stage:

```bash
uv run python scripts/make_corpus.py benign --n 3000   # fictional benign mail
uv run python scripts/make_corpus.py nazario           # Nazario phishing corpus, CC BY 4.0
uv run forgeember batch data/benign-v1 data/nazario/eml --stage scale --live --limit 200
uv run python scripts/make_corpus.py cord              # 1,000 CORD receipt photos, CC BY 4.0
uv run forgeember batch data/cord --stage scale --live --workers 8   # scores totals vs. labels
```

A keyless replay of recorded runs (`site/`) arrives in Phase 4. Live calls need an explicit `--live` flag and a budget stage. The only live script today is `scripts/smoke_test.py`.

## Results

**Scale run, Nov 23–24, 2026**: bulk mode, all on `muse-ember-1.0`, every call in [`traces/calls.jsonl`](traces/calls.jsonl).

| | |
|---|---|
| Emails screened | **6,458**: 3,458 real phishing emails (the [Nazario corpus](https://monkey.org/~jose/phishing/), 2015–2025, CC BY 4.0) plus 3,000 synthetic benign emails. 1 byte-identical duplicate was skipped. |
| Recall on real phishing | **96.6%** (3,340 of 3,458 flagged for review) |
| Precision | **99.7%** (9 false alarms out of 3,000 benign) |
| Model-call cost | **$1.75 for the run, $0.27 per 1,000 emails** (10,000 projects to $2.70) |
| Conservative ledger | $2.78 for the `scale` stage. That includes the 200-item pilot, plus 89 failed or refused calls kept at their full worst-case reservation (mostly 7 crafted PDFs the API could not process). It's reconciled against Meta's Usage page. |
| Time | about 22 minutes with 8 parallel workers |
| Prompt caching | 48% of input tokens served from cache |

Precision and the false-alarm rate are measured against **synthetic** benign mail, which is easier than a real inbox. The recall figure is on real phishing. "Flagged" means the item came out medium or high risk and goes to a person.

**Receipt photos, Nov 24, 2026**: the same bulk pipeline on real phone photos of shop receipts, image in, expense record out.

| | |
|---|---|
| Photos screened | **998** from [CORD v2](https://github.com/clovaai/cord) (CC BY 4.0): creased, shadowed, skewed Indonesian shop receipts, store details blurred by the dataset's authors. 2 exact duplicates were skipped. |
| Totals read correctly | **99.0%** (960 of 970 with a human-labeled total), compared digit for digit. No OCR library: each photo goes to Ember as an image. |
| Routed as receipts | **99.5%** (993 of 998): filed with a note to record the expense, nothing to pay |
| Sent to a person | **5 (0.5%)**: read as invoices, so the payment is held for review. Nothing is ever paid automatically. |
| Model-call cost | **$0.76 for the run, $0.00076 per photo** (2 calls each: triage, then the document specialist) |
| Time | about 7 minutes with 8 parallel workers, 0 errors |

**Labeled fixtures (Gate A):** 13 fictional invoices and emails covering bank-detail changes, CEO impersonation, lookalike domains, Reply-To diversion, tampered IBANs, inflated totals and prompt injection. Routing, invoice fields, risk level and held actions all came out **100%** (skeptic off), for $0.0077. Re-run on Nov 24 with the receipt-aware prompts (`triage.v4`, `document.v2`, skeptic on escalations): **100%** again on all four, for $0.0155. Reports: [`traces/eval/`](traces/eval/).

## Budget & Trace

We have $10 of Meta Model API credit, and going over it disqualifies us. Meta's spend alert only sends an email, so **ForgeEmber enforces its own hard cap of $9.00 in code**:

- **Reserve, then settle.** Before every call, the ledger reserves its worst-case cost: input upper bound × input price + `max_output_tokens` × output price, at the right price tier. It refuses the call if the stage limit or the global cap would be crossed. After the call, it settles at the API's reported usage.
- **Reasoning tokens bill as output.** A failed call, or one with missing usage, keeps its full reservation as spent. An overrun is recorded at its real cost, never trimmed.
- **Stage sub-budgets:** dev $1.50 · eval $1.50 · scale $4.00 · demo $0.75 · reserve $1.25. Moving money between stages needs Billy's named approval and is logged.
- **Every call is one line** in [`traces/calls.jsonl`](traces/calls.jsonl), with tokens (input, cached, output, reasoning), cost, prompt version and sha256, input kind, item id, outcome, latency and the API response id. No secrets, enforced by a pattern check.
- **Reconciled against Meta's own Usage page** with `forgeember budget reconcile`. The ledger must never come in under Meta's bill.

The block below is generated from the ledger by `scripts/render_budget_readme.py`. CI fails if it drifts from the committed snapshot and trace.

<!-- budget:start -->
<!-- Generated by scripts/render_budget_readme.py from traces/budget-snapshot.json. Do not edit by hand: CI fails if this block drifts from the ledger. -->

| Metric | Value |
|---|---|
| Total spent | **$3.5968** of the $10.00 grant |
| Hard cap enforced in code | $9.00 (headroom **$5.4032**) |
| Ember calls recorded | 10,020 (0 over their reservation) |
| Items screened | 7,469 |
| Cost per item | $0.000481 |
| Cost per 1,000 items | $0.4815 |
| Projected monthly cost, small business (3,000 emails) | $1.4444 |
| Prompt-cache hit rate | 44.8% of 14,089,272 input tokens |
| Saved by prompt caching | $0.6060 |
| Reasoning share of output tokens | 65.7% |
| Last reconciliation with Meta's Usage page | 2026-11-24: Meta $2.00, ledger $2.8074 (ledger >= Meta) |

| Stage | Limit | Spent | Open | Remaining |
|---|---:|---:|---:|---:|
| dev | $1.50 | $0.0062 | $0.00 | $1.4938 |
| eval | $1.50 | $0.0384 | $0.00 | $1.4616 |
| scale | $4.00 | $3.5370 | $0.00 | $0.4630 |
| demo | $0.75 | $0.0152 | $0.00 | $0.7348 |
| reserve | $1.25 | $0.00 | $0.00 | $1.25 |

| Input kind | Spent |
|---|---:|
| email | $2.7937 |
| image | $0.7618 |
| pdf | $0.0255 |
| text | $0.0158 |

| Agent role | Spent |
|---|---:|
| cache_probe | $0.0006 |
| coordinator | $1.8915 |
| document | $1.1671 |
| risk | $0.5288 |
| skeptic | $0.0088 |
| smoke | $0.000025 |

![Cumulative Ember spend against the $9.00 hard cap](docs/assets/budget-chart.svg)

Ledger as of: 2026-11-24T21:54:15.837Z. Full per-call trace: `traces/calls.jsonl`.
<!-- budget:end -->

## Replay site (no key needed)

`site/` is a static replay of recorded runs. It shows headline results, each fixture's full decision trail (what code found, what Ember proposed, what code verified, what policy held, what it cost) and the budget evidence. Rebuild it from the committed traces with `uv run forgeember site build`, and host it on any static host (Cloudflare Pages: output directory `site`, no build command).

## Data and licenses

Every dataset's license is checked at its primary source before download. See [`docs/DATA.md`](docs/DATA.md). Demo material uses our own fictional fixtures. Results on public corpora are shown only as aggregate statistics.

## How this was built

Built solo by Billy Davis during the Meta Muse Ember Hackathon (Nov 23–Dec 2, 2026). AI coding assistants: **Muse Code (Muse Spark)** and **Claude (Claude Code)**. Billy approves every plan, holds the API key and merges every pull request. Every line of code was written during the event. Nothing was copied from earlier projects. The session-by-session record is in [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

## License

[MIT](LICENSE)
