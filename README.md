# ForgeEmber

**ForgeEmber screens every email, invoice, receipt photo and voicemail a small business receives, for a fraction of a cent each.** It turns messy input into verified, routed actions, and holds anything risky (payments, bank-detail changes, outside sends) for a person.

> **Ember proposes, and code decides.** Muse Ember extracts, classifies, investigates and proposes. Deterministic code checks the evidence, applies policy, enforces the budget and executes. Only `policy.py` can approve an action, and Ember can never execute a payment, a bank-detail change, an outside send or a deletion.

**Start here: [▶ Invoice MVC-1047, start to finish](https://billyrdavis1985-bot.github.io/ForgeEmber/cases/invoice-1047.html)**, a keyless replay of a recorded live run. A routine invoice waits for approval; a voicemail asks to pay it to new bank details; the payment goes to HIGH with the number to call from your own records; an unrelated invoice stays separate. Then [every decision trail](https://billyrdavis1985-bot.github.io/ForgeEmber/) (what code found, what Ember proposed, what code verified, what policy held, what to do next, what it cost) · demo video: _link goes here on Dec 1_.

## Submission overview

| | |
|---|---|
| **What it is** | A screening pipeline for a small business's inbound mail, documents and voicemail. It extracts fields, detects invoice fraud and bank-detail-change scams, routes the rest, and keeps a full receipt of every decision and every cent spent. |
| **Who it's for** | Small businesses and solo founders with no security team. They get hit hardest by invoice fraud, bank-detail-change scams and impersonation. |
| **Problem statements** | (1) *Cheap enough to run on everything*: the same task over thousands of real items, end to end. (2) *Beyond text, under budget*: emails, PDFs, scans, photos, voicemails and screen recordings in, verified actions out. |
| **Prizes we're aiming for** | Best Frontier Build · Scale the Real World |
| **Model** | Muse Ember (`muse-ember-1.0`) through the Meta Model API, for every agent |
| **Headline result** | **6,458 emails screened for $1.75 ($0.27 per 1,000): 3,340 of 3,458 real phishing emails (96.6%) sent to review, and 9 false alarms on 3,000 synthetic benign emails generated from 9 templates. 998 real receipt photos read straight from the image: 98.7% of totals correct against human labels, for $0.76.** Voicemails, fake-vendor emails and a bank change shown in a screen recording were held correctly in every live run. See Results below. |
| **Demo video** | _Recording Dec 1. Link goes here._ |
| **Try it without a key** | The [replay site](https://billyrdavis1985-bot.github.io/ForgeEmber/) (also in [`site/`](site/index.html)) opens on [invoice MVC-1047, start to finish](https://billyrdavis1985-bot.github.io/ForgeEmber/cases/invoice-1047.html) and shows every decision trail (what code found, what Ember proposed, what code verified, what policy held, what it cost) and the budget evidence. Rebuild it with `uv run forgeember site build`. |
| **Built by** | Built solo by Billy Davis during Nov 23–Dec 2, 2026, with Muse Code (Muse Spark) and Claude as AI coding assistants; no pre-existing code. |

## Meta tooling, and the job each piece does

| Capability | Its job in ForgeEmber | Status |
|---|---|---|
| Responses API on `muse-ember-1.0` | Every agent call, stateless (`store: false`), through one budgeted client | In use: 11,414 live calls, each reserved and settled in the ledger and logged in [`traces/calls.jsonl`](traces/calls.jsonl) |
| Reasoning-effort levels | `minimal` for triage on every item, `low` for specialists, never Ember's default `max` | In use. Reasoning is still 66% of output tokens, so effort is the main cost lever, and it's set on every call |
| Structured output (`text.format` JSON schema) | Strict outputs, validated again by Pydantic | In use: eight strict schemas (triage, invoice/receipt, voicemail, video, identity, risk, skeptic, handoff draft). Every finding quotes its source, and code checks the quote |
| Native PDF and image input | Scanned invoices and receipts straight to structured fields, with no OCR library | Built and run at scale: 998 real receipt photos (CORD), 98.7% of totals correct, $0.76 per 1,000 photos |
| Native audio input | Voicemails straight to actions | Built and run live: Ember listens to the recording itself. All 4 fictional voicemails got the right risk and hold in every run; callback numbers and amounts were heard right, while bank digits read by a robot voice were not, which is why code never lets a recording confirm bank details |
| Tool calling | Read-only lookups the model asks for; code runs them and checks the answer | Built and run live: the identity specialist calls `lookup_vendor` to resolve who an email claims to be from. Code checks the quoted claim, that the vendor came back from a lookup, and the sender's domain. On em-008 it turned a MEDIUM into a HIGH (hosted tools are refused) |
| Prompt caching (cached input $0.004/M) | Stable instructions first, one `prompt_cache_key` per prompt version | In use: 46.3% of 16.5M input tokens served from cache, $0.73 saved, measured per call in the ledger |
| Multi-agent on Ember | A coordinator plus specialists, all on Ember; code decides which run | In use: coordinator, document, voicemail, identity and risk specialists, a skeptic on escalations, and a handoff drafter that writes verification messages from code's facts only |
| Video input (Gate D) | A screen recording of a bank-detail change in a payment portal gets flagged | Built and run live (Nov 26): a video specialist watches the recording (mp4 inline, cost bounded by the token-count endpoint first). On a 10-second portal recording Ember read the payee, sort code and account number right; code found the account isn't the one on file and held the change (HIGH). $0.0014 per recording in Gate A, with the clip cached across the four agents that see it |
| Muse Code | Muse Spark coding sessions and pull-request reviews | In use; trajectory exported with `/export trajectory` for the submission |

## Architecture

```
INPUTS           ENVELOPE            EMBER AGENTS (muse-ember-1.0)        CODE (the only authority)         OUTPUTS
.eml email  -+   id, sha256,   ->    Coordinator: triage, minimal   ->    Sender checks: lookalike    ->    Held for a person (T2):
PDF invoice  |   parts, sniffed      Document: invoice and receipt        domain, impersonation,            payments, bank changes
photo / scan |   mime, text layer      fields from PDFs and photos        Reply-To, injection, file type    Filed and tagged (T0)
voicemail    |   (code reads it      Voicemail: what the caller said      Verify: quotes, totals, IBAN,     Operator notified (T1)
screen rec. -+   first)              Video: what the recording shows      account on file, lookup ids       Receipt per item
                                     Identity: lookup_vendor tool calls   Policy: risk and tiers            Ledger + call trace
                                     Risk + skeptic: on escalation        Budget: reserve, cap, settle      Next step per hold
                                     Handoff: drafts from code's facts
```

Policy tiers: **T0** automatic (file, tag, route a note) · **T1** notify the operator · **T2** hold for human approval (payments, bank-detail changes, outside sends, deletions). Every hold comes with a **next step** written by code from verified facts ("Call Marrowvale Couriers on 01632 960481, the number in your records, not one given in this message"), a drafted verification message to the vendor's address on file that ForgeEmber never sends, and a later message about the same invoice is linked to the hold. Approving a hold marked "check first" needs a written record of how it was checked. Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quickstart (no API key needed)

```bash
uv sync
uv run pytest                 # full test suite: fakes only, no network
uv run forgeember budget      # budget report from the local ledger
uv run python scripts/gen_fixtures.py   # regenerate the 24 fictional, labeled fixtures
```

Screening a real item needs a key, `--live` and a budget stage:

```bash
read -s MODEL_API_KEY && export MODEL_API_KEY
uv run forgeember screen fixtures/emails/em-001-bank-change-lookalike.eml --stage dev --live
uv run forgeember holds        # payments and bank-detail changes waiting for a person
uv run forgeember console      # approval console at http://127.0.0.1:8000 (approve/reject holds)
uv run forgeember screen fixtures/voicemails/vm-002-bank-change-call.eml --stage dev --live   # a voicemail
uv run forgeember eval --stage eval --live   # score all 24 labeled fixtures (Gate A)
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

**One supplier payment, Nov 25–26, 2026**: invoice MVC-1047 from Marrowvale Couriers, then three voicemails: a routine delivery update about 1047, a caller asking to pay 1047 to new bank details, and one about invoice 1052, which isn't open ([`fixtures/scenarios/invoice-1047.json`](fixtures/scenarios/invoice-1047.json)). Code links a later item to an open hold only when the vendor matches and that invoice number appears as a whole token (a spoken "1047" counts). The link can only add caution: the hold's risk can rise, never fall, and the note is code's wording, not the caller's. First live run: 3 of 4, because the new-bank voicemail was read as an invoice document and not linked (fixed: a recording is never read as one). Both runs since: **4 of 4**. The new-bank voicemail raised the invoice's hold from LOW to HIGH, the routine one linked with "nothing new to verify", and 1052 linked to nothing. $0.003–0.006 per run.

After the Nov 26 review, the case also screens the original invoice a second time, and every step checks the hold itself: its risk, its next step, and whether approving needs a recorded check. The first run scored 2 of 5. Ember misheard the caller's company, code resolved no vendor, and the bank-change voicemail didn't link, so the invoice stayed LOW while someone asked to divert its payment. Fixed in code: with no vendor resolved, the open invoice's number alone links (only when exactly one open invoice fits). The next run: **5 of 5**, with the hold still HIGH and the call step after the invoice was screened again ($0.005). Before that fix, a second screening also reset the hold to LOW; now a rescreen never removes caution.

**A corrected invoice, Nov 26, 2026**: Fennimore's open invoice FS-2026-1182 (634.80), then its corrected version (709.80) from Fennimore's domain on file ([`fixtures/scenarios/corrected-invoice.json`](fixtures/scenarios/corrected-invoice.json)). Code linked the two, raised the original to MEDIUM, and gave both holds the same step: "An email gives 709.80 for invoice FS-2026-1182, not the 634.80 on the invoice already waiting. Check the revised charge against your order and confirm it with Fennimore Stationery Ltd on 01632 960150, the number in your records, then approve only one of the two holds for this invoice." **2 of 2**, $0.0023.

**Handoff, Nov 26, 2026**: every hold says what to do next, written by code from verified facts with the contact details on file ("Call Marrowvale Couriers on 01632 960481, the number in your records, not one given in this message… The caller gave 01632960735, which is not that number."). Where the vendor should be asked to confirm, a drafter on Ember writes a short message to the vendor's address on file. It sees only code's facts, never the message under review, and code rejects any draft carrying a link, an address or a long number. ForgeEmber never sends it. Across the 23 recorded trails, 9 got a draft and code rejected none, for $0.0027 in drafter calls so far. Approving a hold marked "check first" needs a written record of how it was checked.

**Screen recording, Nov 26, 2026**: vd-001 is a fictional 10-second, 720p recording of someone editing Marrowvale Couriers' payee in an online-banking portal. Ember read the payee, sort code and account number right. Code found the account isn't on file and held the change: **HIGH**, with the step "Call Marrowvale Couriers on 01632 960481". Each call carried about 3,000–3,400 input tokens. The full trail (four agents plus the draft) cost $0.0025 with nothing cached, and $0.0014 in Gate A, where 80% of the input tokens came from cache. One recording isn't a benchmark; it shows the path works end to end.

**Labeled fixtures (Gate A):** 24 fictional items with known answers (7 invoices, 9 emails, 7 voicemails, 1 screen recording) covering bank-detail changes by email, voice and screen recording, CEO impersonation by email and by voice, lookalike domains, a vendor's name from the wrong domain, Reply-To diversion, tampered IBANs, inflated totals and prompt injection. Bare files reach the model under neutral names ("document.pdf"), so a fixture name like inv-006-marrowvale-bad-iban.pdf can't hint at the answer. **Latest run (Nov 26, blinded, `triage.v5`, the first 23; em-009 was added Nov 27): routing, risk and held actions 100%**, extracted fields 95.3% (the misheard voicemail details), for $0.0280. On Nov 25, on the first 18: the same 100%, fields 93.8%, $0.0186. The first blinded run, on `triage.v4`, failed the 90% routing bar at 88.9%: two payment demands with no invoice attached were routed as invoices (their payments were held either way). `triage.v5` states that difference. Because it was written after seeing those failures, its 100% routing is not a blind test. Earlier runs (file names visible to the model): the first 13 fixtures scored 100% on Nov 23 and 24. Reports: [`traces/eval/`](traces/eval/).

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
| Total spent | **$4.0280** of the $10.00 grant |
| Hard cap enforced in code | $9.00 (headroom **$4.9720**) |
| Ember calls recorded | 11,479 (0 over their reservation) |
| Items screened | 7,480 |
| Cost per item | $0.000538 |
| Cost per 1,000 items | $0.5384 |
| Projected monthly cost, small business (3,000 emails) | $1.6153 |
| Prompt-cache hit rate | 46.2% of 16,624,162 input tokens |
| Saved by prompt caching | $0.7380 |
| Reasoning share of output tokens | 66.4% |
| Last reconciliation with Meta's Usage page | 2026-11-24: Meta $2.00, ledger $2.8074 (ledger >= Meta) |

| Stage | Limit | Spent | Open | Remaining |
|---|---:|---:|---:|---:|
| dev | $1.50 | $0.0062 | $0.00 | $1.4938 |
| eval | $1.50 | $0.3481 | $0.00 | $1.1519 |
| scale | $4.00 | $3.5370 | $0.00 | $0.4630 |
| demo | $0.75 | $0.1339 | $0.00 | $0.6161 |
| reserve | $1.25 | $0.0028 | $0.00 | $1.2472 |

| Input kind | Spent |
|---|---:|
| audio | $0.0873 |
| email | $2.9185 |
| image | $0.9160 |
| pdf | $0.0804 |
| text | $0.0191 |
| video | $0.0067 |

| Agent role | Spent |
|---|---:|
| cache_probe | $0.0006 |
| coordinator | $1.9857 |
| document | $1.3354 |
| handoff | $0.0033 |
| identity | $0.0071 |
| risk | $0.5903 |
| skeptic | $0.0627 |
| smoke | $0.000025 |
| video | $0.0021 |
| voicemail | $0.0409 |

![Cumulative Ember spend against the $9.00 hard cap](docs/assets/budget-chart.svg)

Ledger as of: 2026-11-26T20:21:08.808Z. Full per-call trace: `traces/calls.jsonl`.
<!-- budget:end -->

## Replay site (no key needed)

`site/` is a static replay of recorded runs. It shows headline results, each fixture's full decision trail (what code found, what Ember proposed, what code verified, what policy held, what it cost) and the budget evidence. Rebuild it from the committed traces with `uv run forgeember site build`, and host it on any static host (Cloudflare Pages: output directory `site`, no build command).

## Data and licenses

Every dataset's license is checked at its primary source before download. See [`docs/DATA.md`](docs/DATA.md). Demo material uses our own fictional fixtures. Results on public corpora are shown only as aggregate statistics.

## How this was built

Built solo by Billy Davis during the Meta Muse Ember Hackathon (Nov 23–Dec 2, 2026). AI coding assistants: **Muse Code (Muse Spark)** and **Claude (Claude Code)**. Billy approves every plan, holds the API key and merges every pull request. Every line of code was written during the event. Nothing was copied from earlier projects. The session-by-session record is in [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

## License

[MIT](LICENSE)
