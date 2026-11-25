# ForgeEmber

**ForgeEmber screens every email, invoice, receipt photo and voicemail a small business receives, for a fraction of a cent each.** It turns messy input into verified, routed actions, and holds anything risky (payments, bank-detail changes, outside sends) for a person.

> **Ember proposes, and code decides.** Muse Ember extracts, classifies, investigates and proposes. Deterministic code checks the evidence, applies policy, enforces the budget and executes. Only `policy.py` can approve an action, and Ember can never execute a payment, a bank-detail change, an outside send or a deletion.

## Submission overview

| | |
|---|---|
| **What it is** | A screening pipeline for a small business's inbound mail, documents and voicemail. It extracts fields, detects invoice fraud and bank-detail-change scams, routes the rest, and keeps a full receipt of every decision and every cent spent. |
| **Who it's for** | Small businesses and solo founders with no security team. They get hit hardest by invoice fraud, bank-detail-change scams and impersonation. |
| **Problem statements** | (1) *Cheap enough to run on everything*: the same task over thousands of real items, end to end. (2) *Beyond text, under budget*: emails, PDFs, scans, photos and voicemails in, verified actions out. |
| **Prizes we're aiming for** | Best Frontier Build · Scale the Real World |
| **Model** | Muse Ember (`muse-ember-1.0`) through the Meta Model API, for every agent |
| **Headline result** | **6,458 emails screened for $1.75 ($0.27 per 1,000): 3,340 of 3,458 real phishing emails (96.6%) sent to review, and 9 false alarms on 3,000 synthetic benign emails generated from 9 templates. 998 real receipt photos read straight from the image: 98.7% of totals correct against human labels, for $0.76.** Voicemails and fake-vendor emails were held correctly in every live run. See Results below. |
| **Demo video** | _Recording Dec 1. Link goes here._ |
| **Try it without a key** | The replay site in [`site/`](site/index.html) shows every decision trail (what code found, what Ember proposed, what code verified, what policy held, what it cost) and the budget evidence. Rebuild it with `uv run forgeember site build`. |
| **Built by** | Built solo by Billy Davis during Nov 23–Dec 2, 2026, with Muse Code (Muse Spark) and Claude as AI coding assistants; no pre-existing code. |

## Meta tooling, and the job each piece does

| Capability | Its job in ForgeEmber | Status |
|---|---|---|
| Responses API on `muse-ember-1.0` | Every agent call, stateless (`store: false`), through one budgeted client | In use: 10,249 live calls, each reserved and settled in the ledger and logged in [`traces/calls.jsonl`](traces/calls.jsonl) |
| Reasoning-effort levels | `minimal` for triage on every item, `low` for specialists, never Ember's default `max` | In use. Reasoning is still 66% of output tokens, so effort is the main cost lever, and it's set on every call |
| Structured output (`text.format` JSON schema) | Strict outputs, validated again by Pydantic | In use: six strict schemas (triage, invoice/receipt, voicemail, identity, risk, skeptic). Every finding quotes its source, and code checks the quote |
| Native PDF and image input | Scanned invoices and receipts straight to structured fields, with no OCR library | Built and run at scale: 998 real receipt photos (CORD), 98.7% of totals correct, $0.76 per 1,000 photos |
| Native audio input | Voicemails straight to actions | Built and run live: Ember listens to the recording itself. All 4 fictional voicemails got the right risk and hold in every run; callback numbers and amounts were heard right, while bank digits read by a robot voice were not, which is why code never lets a recording confirm bank details |
| Tool calling | Read-only lookups the model asks for; code runs them and checks the answer | Built and run live: the identity specialist calls `lookup_vendor` to resolve who an email claims to be from. Code checks the quoted claim, that the vendor came back from a lookup, and the sender's domain. On em-008 it turned a MEDIUM into a HIGH (hosted tools are refused) |
| Prompt caching (cached input $0.004/M) | Stable instructions first, one `prompt_cache_key` per prompt version | In use: 45.6% of 14.5M input tokens served from cache, $0.63 saved, measured per call in the ledger |
| Multi-agent on Ember | A coordinator plus specialists, all on Ember; code decides which run | In use: coordinator, document, voicemail, identity and risk specialists, and a skeptic on escalations |
| Video input (stretch) | A screen recording of a bank-detail change in a payment portal gets flagged | Gate D (Nov 30): built only if the reserve stage still holds $1.00 (it holds $1.25) |
| Muse Code | Muse Spark coding sessions and pull-request reviews | In use; trajectory exported with `/export trajectory` for the submission |

## Architecture

```
INPUTS           ENVELOPE            EMBER AGENTS (muse-ember-1.0)        CODE (the only authority)         OUTPUTS
.eml email  -+   id, sha256,   ->    Coordinator: triage, minimal   ->    Sender checks: lookalike    ->    Held for a person (T2):
PDF invoice  |   parts, sniffed      Document: invoice and receipt        domain, impersonation,            payments, bank changes
photo / scan |   mime, text layer      fields from PDFs and photos        Reply-To, injection, file type    Filed and tagged (T0)
voicemail   -+   (code reads it      Voicemail: what the caller said      Verify: quotes, totals, IBAN,     Operator notified (T1)
                 first)              Identity: lookup_vendor tool calls   account on file, lookup ids       Receipt per item
                                     Risk + skeptic: on escalation        Policy: risk and tiers            Ledger + call trace
                                                                          Budget: reserve, cap, settle
```

Policy tiers: **T0** automatic (file, tag, route a note) · **T1** notify the operator · **T2** hold for human approval (payments, bank-detail changes, outside sends, deletions). Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quickstart (no API key needed)

```bash
uv sync
uv run pytest                 # full test suite: fakes only, no network
uv run forgeember budget      # budget report from the local ledger
uv run python scripts/gen_fixtures.py   # regenerate the 18 fictional, labeled fixtures
```

Screening a real item needs a key, `--live` and a budget stage:

```bash
read -s MODEL_API_KEY && export MODEL_API_KEY
uv run forgeember screen fixtures/emails/em-001-bank-change-lookalike.eml --stage dev --live
uv run forgeember holds        # payments and bank-detail changes waiting for a person
uv run forgeember console      # approval console at http://127.0.0.1:8000 (approve/reject holds)
uv run forgeember screen fixtures/voicemails/vm-002-bank-change-call.eml --stage dev --live   # a voicemail
uv run forgeember eval --stage eval --live   # score all 18 labeled fixtures (Gate A)
```

Scale run: build the corpora (free), then screen them in bulk mode, resumable and capped by the `scale` stage:

```bash
uv run python scripts/make_corpus.py benign --n 3000   # fictional benign mail
uv run python scripts/make_corpus.py nazario           # Nazario phishing corpus, CC BY 4.0
uv run forgeember batch data/benign-v1 data/nazario/eml --stage scale --live --limit 200
uv run python scripts/make_corpus.py cord              # 1,000 CORD receipt photos, CC BY 4.0
uv run forgeember batch data/cord --stage scale --live --workers 8   # scores totals vs. labels
```

Live calls need an explicit `--live` flag and a budget stage, and never run in CI. The replay site needs no key: `uv run forgeember site build` rebuilds it from the committed traces.

## Results

**Scale run, Nov 23–24, 2026**: bulk mode, all on `muse-ember-1.0`, every call in [`traces/calls.jsonl`](traces/calls.jsonl).

| | |
|---|---|
| Emails screened | **6,458**: 3,458 real phishing emails (the [Nazario corpus](https://monkey.org/~jose/phishing/), 2015–2025, CC BY 4.0) plus 3,000 synthetic benign emails. 1 byte-identical duplicate was skipped. |
| Recall on real phishing | **96.6%** (3,340 of 3,458 flagged for review) |
| False alarms | **9 of 3,000** synthetic benign emails (99.7% precision). Those emails come from 9 templates, so this measures less than a real inbox would: realistic benign business mail is the evaluation still to do. |
| Model-call cost | **$1.75 for the run, $0.27 per 1,000 emails** (10,000 projects to $2.70) |
| Conservative ledger | $2.78 for the `scale` stage. That includes the 200-item pilot, plus 89 failed or refused calls kept at their full worst-case reservation (mostly 7 crafted PDFs the API could not process). It's reconciled against Meta's Usage page. |
| Time | about 22 minutes with 8 parallel workers |
| Prompt caching | 48% of input tokens served from cache |

Precision and the false-alarm rate are measured against **synthetic** benign mail, which is easier than a real inbox. The recall figure is on real phishing. "Flagged" means the item came out medium or high risk and goes to a person.

**Receipt photos, Nov 24, 2026**: the same bulk pipeline on real phone photos of shop receipts, image in, expense record out.

| | |
|---|---|
| Photos screened | **998** from [CORD v2](https://github.com/clovaai/cord) (CC BY 4.0): creased, shadowed, skewed Indonesian shop receipts, store details blurred by the dataset's authors. 2 exact duplicates were skipped. |
| Totals read correctly | **98.7%** (957 of 970 with a human-labeled total), compared by value: "60.000" and "60000" match, "60.50" and "6050" don't. No OCR library: each photo goes to Ember as an image. A first, digits-only scorer reported 99.0% because it counted 3 such misreadings as right; the saved outputs were rescored ([`scripts/rescore_receipts.py`](scripts/rescore_receipts.py)). |
| Blinded check | 200 of the photos re-run with neutral file names, so the model never saw a dataset name: **98.5%** of totals, 99.5% routed as receipts, 1 sent for review, $0.15. |
| Routed as receipts | **99.5%** (993 of 998): filed with a note to record the expense, nothing to pay |
| Sent to a person | **5 (0.5%)**: read as invoices, so the payment is held for review. Nothing is ever paid automatically. |
| Model-call cost | **$0.76 for the run, $0.00076 per photo** (2 calls each: triage, then the document specialist) |
| Time | about 7 minutes with 8 parallel workers, 0 errors |

**Voicemails, Nov 24, 2026**: 4 fictional voicemails, spoken by a text-to-speech voice, sent to Ember as audio (MP3, and a WAV attached to a phone-system email).

| Voicemail | Decision (same in every run) | What Ember heard |
|---|---|---|
| Courier moves a delivery | LOW, filed as a routine voicemail | callback number right; "Marrow Vale Couriers" (same sound as Marrowvale) |
| "Kestrel Byte Hosting" reads out new bank details | **HIGH, bank change held**; the account read out isn't on file (the spoken digits are unverified) | company name right in 1 of 4 runs ("Kestrel Bite Posting" in the others); sort code right once, account number wrong every time |
| Caller using the owner's name asks for gift cards | **HIGH, payment held** | callback number and amount right |
| Customer places an order | LOW, filed | callback number right |

Decisions and holds were right in all 4 runs. The details weren't always: a robot voice reading digits is hard to hear. That's the design point. A recording is a claim, so a voice can't lower risk or confirm bank details: any money or bank request from a voicemail is at least MEDIUM and held for a call back on a number on file. Company names heard in a recording are matched by sound ("Bite Posting" to "Byte Hosting"), so the account read out still gets checked against the vendor's records. Cost: about $0.0004 for a routine voicemail and $0.002 for a fraud attempt (4 agents).

**Tool calling, Nov 24, 2026**: em-008 is a payment demand signed "Fennimore Stationery" but sent from `fmr-supplies.example`, with no bank details to compare, so code alone rates it MEDIUM. Ember called `lookup_vendor("Fennimore Stationery")` and code answered from our records, without bank details. Ember then named the vendor id, and code checked three things: the claim is quoted verbatim from the email, the id came back from that lookup, and the sender's domain is not one of Fennimore's. Result: **HIGH, payment held**. On em-007 (a vendor we've never dealt with) Ember looked up the name, then, on its own, the domain; both came back not found, so it claimed no vendor. Each identity check took 2–3 rounds and cost under $0.0005.

**Labeled fixtures (Gate A):** 18 fictional items with known answers (6 invoices, 8 emails, 4 voicemails) covering bank-detail changes, CEO impersonation by email and by voice, lookalike domains, a vendor's name from the wrong domain, Reply-To diversion, tampered IBANs, inflated totals and prompt injection. Bare files reach the model under neutral names ("document.pdf"), so a fixture name like inv-006-marrowvale-bad-iban.pdf can't hint at the answer. **Latest run (Nov 25, blinded, `triage.v5`): routing, risk and held actions 100%**, extracted fields 93.8% (the misheard voicemail details), for $0.0186. The first blinded run, on `triage.v4`, failed the 90% routing bar at 88.9%: two payment demands with no invoice attached were routed as invoices (their payments were held either way). `triage.v5` states that difference. Because it was written after seeing those failures, its 100% routing is not a blind test. Earlier runs (file names visible to the model): the first 13 fixtures scored 100% on Nov 23 and 24. Reports: [`traces/eval/`](traces/eval/).

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
| Total spent | **$3.8691** of the $10.00 grant |
| Hard cap enforced in code | $9.00 (headroom **$5.1309**) |
| Ember calls recorded | 10,878 (0 over their reservation) |
| Items screened | 7,474 |
| Cost per item | $0.000518 |
| Cost per 1,000 items | $0.5176 |
| Projected monthly cost, small business (3,000 emails) | $1.5528 |
| Prompt-cache hit rate | 45.8% of 15,698,776 input tokens |
| Saved by prompt caching | $0.6903 |
| Reasoning share of output tokens | 66.2% |
| Last reconciliation with Meta's Usage page | 2026-11-24: Meta $2.00, ledger $2.8074 (ledger >= Meta) |

| Stage | Limit | Spent | Open | Remaining |
|---|---:|---:|---:|---:|
| dev | $1.50 | $0.0062 | $0.00 | $1.4938 |
| eval | $1.50 | $0.2717 | $0.00 | $1.2283 |
| scale | $4.00 | $3.5370 | $0.00 | $0.4630 |
| demo | $0.75 | $0.0542 | $0.00 | $0.6958 |
| reserve | $1.25 | $0.00 | $0.00 | $1.25 |

| Input kind | Spent |
|---|---:|
| audio | $0.0253 |
| email | $2.8640 |
| image | $0.9132 |
| pdf | $0.0509 |
| text | $0.0158 |

| Agent role | Spent |
|---|---:|
| cache_probe | $0.0006 |
| coordinator | $1.9531 |
| document | $1.3062 |
| identity | $0.0034 |
| risk | $0.5563 |
| skeptic | $0.0329 |
| smoke | $0.000025 |
| voicemail | $0.0164 |

![Cumulative Ember spend against the $9.00 hard cap](docs/assets/budget-chart.svg)

Ledger as of: 2026-11-25T02:22:03.276Z. Full per-call trace: `traces/calls.jsonl`.
<!-- budget:end -->

## Replay site (no key needed)

`site/` is a static replay of recorded runs. It shows headline results, each fixture's full decision trail (what code found, what Ember proposed, what code verified, what policy held, what it cost) and the budget evidence. Rebuild it from the committed traces with `uv run forgeember site build`, and host it on any static host (Cloudflare Pages: output directory `site`, no build command).

## Data and licenses

Every dataset's license is checked at its primary source before download. See [`docs/DATA.md`](docs/DATA.md). Demo material uses our own fictional fixtures. Results on public corpora are shown only as aggregate statistics.

## How this was built

Built solo by Billy Davis during the Meta Muse Ember Hackathon (Nov 23–Dec 2, 2026). AI coding assistants: **Muse Code (Muse Spark)** and **Claude (Claude Code)**. Billy approves every plan, holds the API key and merges every pull request. Every line of code was written during the event. Nothing was copied from earlier projects. The session-by-session record is in [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

## License

[MIT](LICENSE)
