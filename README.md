# ForgeEmber

**ForgeEmber screens every email, invoice, receipt photo, voicemail and screen recording a small business receives, for a fraction of a cent each, and holds every payment and bank-detail change for a person, with a concrete next step.**

Small businesses lose money to invoice fraud, "our bank details have changed" messages and fake calls from the boss. They have no security team, and checking every item by hand is too slow. ForgeEmber checks all of it, on Muse Ember.

> **Ember proposes; code decides; a person acts.** Ember reads, listens and proposes. Deterministic code verifies every claim against the source and the business's own records, and only `policy.py` decides. Payments, bank-detail changes, outside sends and deletions are always held, never executed.

**▶ Start here: [Invoice MVC-1047, start to finish](https://billyrdavis1985-bot.github.io/ForgeEmber/cases/invoice-1047.html)**, a two-minute keyless replay of a recorded live run · [every decision trail and the budget evidence](https://billyrdavis1985-bot.github.io/ForgeEmber/) (also in [`site/`](site/index.html)) · demo video: _link goes here on Dec 1_

## Results at a glance

Measured on recorded live runs with `muse-ember-1.0`; each row links to its evidence in this repo. The cost row is a projection from the measured per-message cost.

| What | Result | Evidence |
|---|---|---|
| **Real phishing, at scale** | 3,340 of 3,458 real phishing emails (96.6%) sent to review; 118 missed. 6,458 emails in all for $1.75 ($0.27 per 1,000) | [`traces/scale/`](traces/scale/) |
| **Real receipt photos** | 998 photos read straight from the image: 98.7% of totals correct against human labels, for $0.76 | [`traces/scale/`](traces/scale/) |
| **One supplier payment, end to end** | A voicemail asking to divert invoice 1047 turned the waiting payment HIGH, with the number to call from the business's own records; an unrelated invoice stayed separate; screening the invoice again kept the caution. 5 of 5 steps | [replay](https://billyrdavis1985-bot.github.io/ForgeEmber/cases/invoice-1047.html) |
| **Frozen attack suite** | 10 of 10 attacks defended by the defense each one names: look-alike letters, hidden PDF text, instructions to the AI in a PDF, a photo and a voicemail, an account number posing as an invoice number | [`traces/attacks/`](traces/attacks/) |
| **Ordinary mail** (the false-alarm question) | On 25 tricky but legitimate messages, labeled before running: 2 of 18 routine ones interrupted the owner; 0 of 7 that needed a check were missed | [`traces/ordinary/`](traces/ordinary/) |
| **Labeled fixtures (Gate A)** | 25 of 25 right on risk and held actions, including fraud by voicemail, a fake vendor caught through a tool call and a bank change in a screen recording; routing 100%, fields 94.6% | [`traces/eval/`](traces/eval/) |
| **What it costs a business** | About $0.50 a month for 20 items a day (our assumption), at the measured $0.0008 per message | [`traces/ordinary/`](traces/ordinary/) |
| **Budget** | $4.13 of a $9.00 hard cap enforced in code (the grant is $10). Every call is one line in the trace, reconciled against Meta's Usage page | [Budget & Trace](#budget--trace) |

**Limits, stated up front:** the 3,000 benign emails in the scale run are synthetic (9 templates); the ordinary-mail and attack sets were written by us, with labels fixed before running; Gate A's routing prompt was revised after seeing earlier failures, so its routing score isn't a blind test. Details in [Results](#results).

## Hackathon fit

- **Problem statements:** (1) *Cheap enough to run on everything*: the same task over thousands of real items, end to end. (2) *Beyond text, under budget*: emails, PDFs, scans, photos, voicemails and screen recordings in, verified actions out.
- **Prizes we're aiming for:** Best Frontier Build · Scale the Real World
- **Model:** Muse Ember (`muse-ember-1.0`) through the Meta Model API, for every agent
- **Built by:** Billy Davis, solo, Nov 23–Dec 2, 2026, with Muse Code (Muse Spark) and Claude as AI coding assistants; no pre-existing code

## Meta tooling, and the job each piece does

| Capability | Its job in ForgeEmber | Status |
|---|---|---|
| Responses API on `muse-ember-1.0` | Every agent call, stateless (`store: false`), through one budgeted client | In use: over 11,700 live calls, each reserved and settled in the ledger and logged in [`traces/calls.jsonl`](traces/calls.jsonl) |
| Reasoning-effort levels | `minimal` for triage on every item, `low` for specialists, never Ember's default `max` | In use. Reasoning is still 66% of output tokens, so effort is the main cost lever, and it's set on every call |
| Structured output (`text.format` JSON schema) | Strict outputs, validated again by Pydantic | In use: eight strict schemas (triage, invoice/receipt, voicemail, video, identity, risk, skeptic, handoff draft). Every finding quotes its source, and code checks the quote |
| Native PDF and image input | Scanned invoices and receipts straight to structured fields, with no OCR library | Built and run at scale: 998 real receipt photos (CORD), 98.7% of totals correct, $0.76 per 1,000 photos |
| Native audio input | Voicemails straight to actions | Built and run live: Ember listens to the recording itself. All 4 fictional voicemails got the right risk and hold in every run; callback numbers and amounts were heard right, while bank digits read by a robot voice were not, which is why code never lets a recording confirm bank details |
| Tool calling | Read-only lookups the model asks for; code runs them and checks the answer | Built and run live: the identity specialist calls `lookup_vendor` to resolve who an email claims to be from. Code checks the quoted claim, that the vendor came back from a lookup, and the sender's domain. On em-008 it turned a MEDIUM into a HIGH (hosted tools are refused) |
| Prompt caching (cached input $0.004/M) | Stable instructions first, one `prompt_cache_key` per prompt version | In use: about 46% of 17M input tokens served from cache, over $0.75 saved, measured per call in the ledger |
| Multi-agent on Ember | A coordinator plus specialists, all on Ember; code decides which run | In use: coordinator, document, voicemail, identity and risk specialists, a skeptic on escalations, and a handoff drafter that writes verification messages from code's facts only |
| Video input (Gate D) | A screen recording of a bank-detail change in a payment portal gets flagged | Built and run live (Nov 26): a video specialist watches the recording (mp4 inline, cost bounded by the token-count endpoint first). On a 10-second portal recording Ember read the payee, sort code and account number right; code found the account isn't the one on file and held the change (HIGH). $0.0014 per recording in Gate A, with the clip cached across the four agents that see it |
| Muse Code | Muse Spark coding sessions and pull-request reviews | In use: Spark cross-reviewed the security code (fixes in PRs #37 and earlier). Trajectory exported with `/export trajectory` as `traces/muse-code-trajectory.json` for the submission. (`traces/usage-trajectory.json` is something else: our own spend history, built from the call trace by `scripts/export_usage_trajectory.py`.) |

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
uv run python scripts/gen_fixtures.py   # regenerate the 25 fictional, labeled fixtures
```

Screening a real item needs a key, `--live` and a budget stage:

```bash
read -s MODEL_API_KEY && export MODEL_API_KEY
uv run forgeember screen fixtures/emails/em-001-bank-change-lookalike.eml --stage dev --live
uv run forgeember holds        # payments and bank-detail changes waiting for a person
uv run forgeember console      # approval console at http://127.0.0.1:8000 (approve/reject holds)
uv run forgeember screen fixtures/voicemails/vm-002-bank-change-call.eml --stage dev --live   # a voicemail
uv run forgeember eval --stage eval --live   # score all 25 labeled fixtures (Gate A)
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

All runs are on `muse-ember-1.0`, in 2026; every call is one line in [`traces/calls.jsonl`](traces/calls.jsonl). Each result gives the number, how it was measured, its limits and its evidence. How each one got there, including what failed first, is in [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

### At scale

#### Real phishing (Nov 23–24)

| | |
|---|---|
| Result | **3,340 of 3,458 real phishing emails (96.6%) sent to review; 118 missed.** 9 false alarms in 3,000 synthetic benign emails |
| Cost | **$1.75 for 6,458 emails, $0.27 per 1,000**; about 22 minutes with 8 workers; 48% of input tokens served from cache |
| How measured | Bulk mode. Phishing: the [Nazario corpus](https://monkey.org/~jose/phishing/) (2015–2025, CC BY 4.0). "Sent to review" means rated MEDIUM or HIGH, so a person sees it. 1 byte-identical duplicate skipped |
| Limits | The benign side is synthetic (9 templates) and easier than a real inbox; [Ordinary mail](#ordinary-mail-the-hard-kind-nov-27) below measures that question differently. The `scale` stage's ledger total is $2.78, not $1.75: it includes the 200-item pilot and 89 failed or refused calls kept at their full worst-case reservation (mostly 7 crafted PDFs the API couldn't process) |
| Evidence | [`traces/scale/`](traces/scale/) |

#### Real receipt photos (Nov 24)

| | |
|---|---|
| Result | **998 photos read straight from the image: 98.7% of totals correct** (957 of 970 with a human label). 99.5% routed as receipts (record the expense, nothing to pay); the other 5 were read as invoices, so their payments were held. Nothing is ever paid automatically |
| Cost | **$0.76 for the run, $0.00076 per photo** (2 calls each); about 7 minutes, 0 errors |
| How measured | [CORD v2](https://github.com/clovaai/cord) (CC BY 4.0): creased, shadowed, skewed phone photos of Indonesian shop receipts. Each photo goes to Ember as an image; no OCR library. Totals compared by value: "60.000" and "60000" match, "60.50" and "6050" don't |
| Limits | A first, digits-only scorer reported 99.0% by counting 3 such misreadings as right; the saved outputs were rescored ([`scripts/rescore_receipts.py`](scripts/rescore_receipts.py)). A blinded re-run of 200 photos under neutral file names: 98.5% of totals, $0.15 |
| Evidence | [`traces/scale/`](traces/scale/) |

### The payment workflow

#### One supplier payment, end to end: invoice 1047 (Nov 25–27)
- **Result: 5 of 5 steps, live.** A voicemail asking to pay invoice 1047 to new bank details turned the waiting payment from LOW to HIGH, with "Call Marrowvale Couriers on 01632 960481, the number in your records, not one given in this message… The caller gave 01632960735, which is not that number." A voicemail about invoice 1052 stayed separate. Screening the invoice again kept the caution. [Replay](https://billyrdavis1985-bot.github.io/ForgeEmber/cases/invoice-1047.html)
- **How it works:** code links a later item to an open payment when that invoice's number appears in it as a whole token (a spoken "1047" counts) and the vendor matches, or, if no vendor can be resolved, when exactly one open invoice fits. A link can only add caution, and its note is code's wording, never the caller's.
- **Limits:** three live failures were found and fixed in code on the way (a recording read as an invoice; a misheard company that blocked the link; a second screening that reset the hold to LOW). Fixtures are fictional.
- **Evidence:** [`fixtures/scenarios/invoice-1047.json`](fixtures/scenarios/invoice-1047.json), [`traces/scenarios/`](traces/scenarios/)

#### A corrected invoice, and the same invoice twice (Nov 26–27)
- **Result: both 2 of 2, live.** A corrected invoice with a higher total gets its own step on both holds ("…not the 634.80 on the invoice already waiting. Check the revised charge against your order… then approve only one of the two holds for this invoice"), and the original rises to MEDIUM. A copy of an invoice re-sent by email is MEDIUM with "approve only one of the two", and the original is warned too. [Replay](https://billyrdavis1985-bot.github.io/ForgeEmber/cases/duplicate-invoice.html)
- **How it works:** before a payment is filed, code checks the hold history for the same vendor: the same invoice number already approved ("this may ask for the same money twice"), the same number and amount already waiting, or the same amount under a new number within 14 days. Rejected payments don't count, monthly bills (about 30 days apart) don't trip it, and `policy.py` rates a duplicate MEDIUM.
- **Evidence:** [`fixtures/scenarios/`](fixtures/scenarios/), [`traces/scenarios/`](traces/scenarios/)

#### What the owner is told
- **Every hold has a next step,** written by code from verified facts and the contact details on file, never from the message under review. Where the vendor should confirm, a drafter on Ember writes a short message to the vendor's address on file; it sees only code's facts, code rejects any draft with a link, an address or a long number, and ForgeEmber never sends it. Approving a hold marked "check first" needs a written record of how it was checked, or the console refuses it.
- **Result (Nov 26):** across the 23 recorded trails, 9 got a draft and code rejected none; $0.0027 in drafter calls.

### Beyond text

| Input | Result (live) | Cost | Limits |
|---|---|---|---|
| **Voicemails** (4, text-to-speech; MP3 and a WAV attached to an email) | Decisions and holds right in all 4 runs: a bank change read out and a caller using the owner's name are HIGH and held; routine calls are filed | $0.0004 routine, $0.002 fraud attempt | Spoken digits were often misheard, so a recording never confirms bank details; company names are matched by sound ("Kestrel Bite Posting" to Kestrel Byte Hosting) |
| **Screen recording** (1, 10 s, 720p) | Payee, sort code and account read right; the account isn't on file, so HIGH and the change held | $0.0025 uncached, $0.0014 with 80% cached | One recording isn't a benchmark; it shows the path works end to end |
| **Tool calling** (`lookup_vendor`) | em-008, "Fennimore Stationery" writing from `fmr-supplies.example`: Ember looked the name up; code verified the quoted claim, that the vendor id came from that lookup, and that the domain isn't Fennimore's: HIGH, not MEDIUM | under $0.0005, 2–3 rounds | Lookups return names, domains and usual amounts, never bank details |

### Robustness

#### Frozen attack suite (Nov 26)
- **Result: 10 of 10 defended live by the defense each one names; 10 of 10 held; $0.019.**
- **How measured:** 10 fictional attacks, frozen before any run ([`fixtures/attacks/`](fixtures/attacks/manifest.json)): a punycode sender domain, a longer lookalike domain, white-text instructions to the AI, a PDF whose invisible text layer shows the account on file while the page shows another, a note to the AI in a photo and one in a voicemail, zero-width characters in an IBAN, an account number posing as an invoice number to reach the drafted message, a request to put a payment link in the reply, and a vendor name with a Cyrillic letter. Fixes went in code, never in a prompt.
- **What it found:** before the fixes, with a model that noticed nothing, the doctored PDF came out LOW ("approve when it's due"), the account number reached the drafted message, and both lookalike domains were only "not one of your vendors". With the fixes, code alone defends 9 of 10 against that blind model; the note in a photo needs Ember to report it, since code can't read pixels (it was held either way, and live Ember reported it).
- **Limits:** we wrote the attacks; the PDF scan reads page content streams, not form XObjects.
- **Evidence:** [`traces/attacks/`](traces/attacks/)

#### Ordinary mail, the hard kind (Nov 27)
- **Result: 2 of 18 routine messages interrupted the owner for nothing; 0 of 7 that needed a check were missed; $0.079 per 100 messages.**
- **How measured:** 25 legitimate messages a small business really gets ([`fixtures/ordinary/`](fixtures/ordinary/manifest.json)), labeled before any run. Routine: reminders, a "FINAL REMINDER" with a late fee, a statement, invoices, the owner forwarding one, a password-expiry notice, the bank, a credit note, a corrected invoice. Check: a genuine bank change, an invoice far above usual, a new supplier, an accounts-email change, a wrong subtotal, a redelivery fee by voicemail, the owner paying a new contractor. Measured once; nothing tuned to it.
- **Why the two:** a deliberate rule, that a model can raise risk but never lower it. Triage quoted real urgency ("a GBP 10.00 late fee is added if payment isn't received by Friday"; the owner's "Can we pay Marrowvale's invoice 1047 today"); code found the quotes in the message, so they counted as MEDIUM though the second reader said benign.
- **Limits:** written by the builder's AI assistant, so not an independent test.
- **Evidence:** [`traces/ordinary/`](traces/ordinary/)

### Labeled fixtures (Gate A, Nov 27)
- **Result: 25 of 25 right on risk and held actions; routing 100%; extracted fields 94.6%; $0.0297.** Every Gate A run since Nov 25 has scored 100% on risk and holds as the set grew from 18 to 25.
- **How measured:** 25 fictional items with known answers (7 invoices, 10 emails, 7 voicemails, 1 screen recording): bank-detail changes by email, voice and screen recording, CEO impersonation by email and voice, lookalike domains, a vendor's name from the wrong domain, Reply-To diversion, tampered IBANs, inflated totals, prompt injection, a corrected and a re-sent invoice. Bare files reach the model under neutral names ("document.pdf"), so a file name can't hint at the answer.
- **Limits:** the routing prompt (`triage.v5`) was revised after `triage.v4` failed the routing bar (88.9%), so its routing score isn't a blind test. Fields fall short where spoken digits were misheard.
- **Evidence:** [`traces/eval/`](traces/eval/)

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
| Total spent | **$4.1273** of the $10.00 grant |
| Hard cap enforced in code | $9.00 (headroom **$4.8727**) |
| Ember calls recorded | 11,788 (0 over their reservation) |
| Items screened | 7,516 |
| Cost per item | $0.000549 |
| Cost per 1,000 items | $0.5491 |
| Projected monthly cost, small business (3,000 emails) | $1.6472 |
| Prompt-cache hit rate | 45.9% of 17,157,631 input tokens |
| Saved by prompt caching | $0.7567 |
| Reasoning share of output tokens | 66.4% |
| Last reconciliation with Meta's Usage page | 2026-11-24: Meta $2.00, ledger $2.8074 (ledger >= Meta) |

| Stage | Limit | Spent | Open | Remaining |
|---|---:|---:|---:|---:|
| dev | $1.50 | $0.0062 | $0.00 | $1.4938 |
| eval | $1.50 | $0.4455 | $0.00 | $1.0545 |
| scale | $4.00 | $3.5370 | $0.00 | $0.4630 |
| demo | $0.75 | $0.1359 | $0.00 | $0.6141 |
| reserve | $1.25 | $0.0028 | $0.00 | $1.2472 |

| Input kind | Spent |
|---|---:|
| audio | $0.1062 |
| email | $2.9716 |
| image | $0.9195 |
| pdf | $0.0992 |
| text | $0.0203 |
| video | $0.0104 |

| Agent role | Spent |
|---|---:|
| cache_probe | $0.0006 |
| coordinator | $2.0080 |
| document | $1.3606 |
| handoff | $0.0045 |
| identity | $0.0092 |
| risk | $0.6119 |
| skeptic | $0.0812 |
| smoke | $0.000025 |
| video | $0.0028 |
| voicemail | $0.0485 |

![Cumulative Ember spend against the $9.00 hard cap](docs/assets/budget-chart.svg)

Ledger as of: 2026-11-27T00:16:43.918Z. Full per-call trace: `traces/calls.jsonl`.
<!-- budget:end -->

## Replay site (no key needed)

`site/` is a static replay of recorded runs. It shows headline results, each fixture's full decision trail (what code found, what Ember proposed, what code verified, what policy held, what it cost) and the budget evidence. Rebuild it from the committed traces with `uv run forgeember site build`, and host it on any static host (Cloudflare Pages: output directory `site`, no build command).

## Data and licenses

Every dataset's license is checked at its primary source before download. See [`docs/DATA.md`](docs/DATA.md). Demo material uses our own fictional fixtures. Results on public corpora are shown only as aggregate statistics.

## How this was built

Built solo by Billy Davis during the Meta Muse Ember Hackathon (Nov 23–Dec 2, 2026). AI coding assistants: **Muse Code (Muse Spark)** and **Claude (Claude Code)**. Billy approves every plan, holds the API key and merges every pull request. Every line of code was written during the event. Nothing was copied from earlier projects. The session-by-session record is in [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md).

## License

[MIT](LICENSE)
