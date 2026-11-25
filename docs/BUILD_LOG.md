# Build log

One dated entry per session: who worked, what changed, decisions, spend delta, next steps. This is the handoff between sessions. Newest entry last.

---

## 2026-11-23: Session 1, Phase 0 foundation

**Who:** Claude (Claude Code, cloud session), with Billy approving. No Muse Code session yet.

**Spend delta:** **$0.00.** No live calls were made. `scripts/smoke_test.py` was written but **not run**.

### What changed
- Repository seeded: `main` holds one empty root commit, and all Phase 0 work is on `phase-0-foundation`.
- Docs: `INTENT`, `ARCHITECTURE` (ADR-0001), `PLAN`, `RULES`, `BUDGET`, `DATA`, `META_API_NOTES` and this log. `AGENTS.md` is the single rules file (Muse Code reads it); `CLAUDE.md` imports it.
- Scaffold: `pyproject.toml` (uv, Python 3.12, `forgeember` console script), the layout from the kickoff prompt with documented stub modules, `.gitignore`, `.env.example`, MIT `LICENSE`, `CHANGELOG.md`, `Dockerfile`, and CI (ruff + pytest on push and PR).
- Budget module, complete and tested: exact price table, reserve-then-settle ledger, stage limits plus the $9.00 global cap, refusal past either, reasoning billed as output, failed calls keeping their reservation, thread and process safety, JSONL trace, transfers, reconciliation, orphan expiry, report and CLI, README block and chart, CI drift check, submission export.
- Budgeted Ember client (`ember/client.py`), the only network module, tested with a fake transport.
- README skeleton with the judge-facing overview and a generated $0.00 Budget & Trace block.

### Decisions
1. **`main` seeding:** Billy approved a one-time push of a single empty root commit to `main`, so PRs have a base. Nothing else goes to `main`.
2. **Cloud environment:** Billy approved a personal environment, "ForgeEmber hackathon", allowlisting `dev.meta.ai`, `developer.meta.com` and the dataset primary-source hosts. Meta's docs and the license pages were unreachable before that.
3. **Money is integer nano-dollars.** Every Ember price is an exact integer per token at that scale.
4. **"200K" is read as 200,000 input tokens** (cached included), which is the conservative reading.
5. **Billable output = max(output_tokens, reasoning_tokens).** Meta's API reference says reasoning is "counted within the output token total", so this never double-bills and never under-counts.
6. **Any call without trusted usage keeps its full reservation**, including 4xx rejections. Whether Meta bills rejected calls is UNVERIFIED, so we stay conservative until reconciliation shows otherwise.
7. **Stateless calls only** (`store: false`, no `previous_response_id`). With server-side history, `usage` reports only the new turn.
8. **Reasoning effort is mandatory on every call.** Ember's default is `"max"`.
9. **Hosted tools are refused** (`web_search` costs $2.50/1K queries and would bypass policy). `tool_choice` only supports `"auto"` on Meta's API, so policy must handle both a tool call and a plain answer.
10. **Input-token upper bound:** for text, UTF-8 bytes + 512 + 32 per item. Media calls must pass an explicit bound, which Phase 1 measures with `POST /v1/responses/input_tokens`.
11. **Raw httpx, no OpenAI SDK.** It keeps the single network boundary small. FastAPI/HTMX are deferred to Phase 4 (console) to keep dependencies minimal.
12. **Datasets:** the Nazario phishing corpus (CC BY 4.0) and CORD (CC BY 4.0) are usable with attribution. Enron (no license), SpamAssassin (no grant), FUNSD (non-commercial), SROIE (unverifiable) and the HF compilations are **not used**. See `docs/DATA.md`.

### Open questions for Billy
1. **Benign-mail corpus:** with Enron and SpamAssassin excluded, there's no clearly licensed real benign business email. Should we (a) generate benign mail procedurally in code, (b) make an explicit exception for Enron, or (c) keep searching?
2. **Muse Code billing:** on pay-as-you-go, Muse Code bills the same team account. Its default model is `muse-spark-1.2`, at 12.5× Ember's input price and 8.5× its output price. Whether hackathon credits cover it is undocumented. Plan: a Muse Code subscription (billed separately), or pay-as-you-go pinned to `--model muse-ember-1.0`? Should Muse Code spend come out of a stage, e.g. `reserve`?
3. **Reservation on 4xx:** keep counting rejected calls (400/429) at the full reservation, or release them once reconciliation shows they aren't billed?
4. **Commit signatures:** this cloud session signs commits with its own key, so GitHub may show them as "Unverified" even though the author is Billy. Keep that, or turn signing off for this repo?

### Next steps
1. Billy reviews `phase-0-foundation` (Spark reviews the PR per the review rule).
2. Billy runs `uv run python scripts/smoke_test.py --live --stage dev`, then `uv run forgeember budget`, then `uv run python scripts/render_budget_readme.py`, and commits `traces/` and the README.
3. Phase 1 (11/24–25): envelope, schemas, agents, verifier, policy, baselines, and one vertical slice (invoice PDF → extraction → verify → HOLD → receipt). Measure the PDF input-token cost with the count endpoint first.

---

## 2026-11-23: Session 2, Spark post-merge review of Phase 0

**Who:** Spark (Muse Code). PR #1 was merged to `main` without review, so this is a post-merge review of `main` at `35e07a1`, per the review rule. Findings and fixes are on `spark-review-phase-0`, PR open for Claude. Billy merges.

**Spend delta:** **$0.00.** No live calls. Docs re-verified against Meta's live pages (pricing, reasoning, caching, API reference, model page) — reads only, no API key used.

### Findings (all fixed on the branch)

1. **`actual_cost` under-counted malformed usage (`pricing.py`).** When a response reported `cached_tokens > input_tokens`, the old rule billed everything at the cheap cached rate (e.g. 150 × $0.004/M = 600 nUSD for input that implies ≥ 100 uncached tokens = 10,000 nUSD). That broke the module's own "never under-count" rule. Now the larger figure bills at the full uncached rate. Normal-case billing is unchanged. The old test asserted the buggy split; it now asserts the conservative one.
2. **`prompt_cache_key` wasn't counted in the input-token estimate (`client.py`).** Every other string we send (instructions, input, tool/schema JSON) is counted. The key is almost certainly not tokenized, but the estimate claims to be an upper bound, so its bytes are counted now. `docs/BUDGET.md` updated to match.
3. **Malformed `input` shapes crashed with `AttributeError` (`client.py`).** Non-mapping items, dict/scalar `content`, and non-mapping blocks escaped the "validate request" step with confusing errors (pre-reserve, so money-safe, but sloppy). They now raise `ValueError` with a clear message.
4. **The safety-net settle could mask the real error (`client.py`).** If the in-`try` settle committed to the DB and then the trace write failed (e.g. a hostile response `id` shaped like a secret), the `finally` retry raised `LedgerStateError` ("already settled") and hid the original error. The `finally` now swallows only `LedgerStateError` — the money stays counted and the original error propagates.

### Checked and cleared (no change)

- **Limit math:** committed = settled cost + open reservations; stage and global checks plus the insert run in one `BEGIN IMMEDIATE` transaction (atomic across threads and processes); refusals write no row; overruns record actuals and flag. Nothing lets new spend past $9.00.
- **Tier selection and `max(output, reasoning)`:** match Meta's docs, re-verified live today (tier by input tokens cached-included, long-context rates apply in full; reasoning "counted within the output token total"; `max_output_tokens` caps reasoning + visible output together, min 16).
- **Request body vs `docs/META_API_NOTES.md`:** endpoint, fields, `store: false`, required effort, refused hosted tools, usage parsing, and settlement on every error path (transport, 4xx/5xx, missing usage, unexpected exception, crash orphan) all match. No path sends without a reservation or leaves a reservation silently open.
- **API notes:** every load-bearing "verified" claim re-checked against Meta's live docs matches. One nuance for Phase 1: the pricing page says injected steering tokens are excluded from `usage` *and* the count endpoint, while the caching page says the count endpoint includes prompt scaffolding. Both agree `usage` excludes them (what the ledger settles from), and any count-endpoint overage is the safe direction for reservations.

### Decisions

1. Malformed usage distrusts the cheap rate entirely (bill `max` at full rate) rather than trying to salvage the reported split.
2. Left as notes, not fixes: the unchecked WAL `PRAGMA` result (correctness holds in rollback-journal mode too), and two ledgers with different configs on one DB (out of the single-operator threat model).

### Next steps

1. Claude reviews the `spark-review-phase-0` PR; Billy merges.
2. Billy runs the smoke test per Session 1 next steps, then reconciles against the Usage page.
3. Phase 1 decides the benign-mail corpus question (Session 1 open question 1) before the scale run.

---

## 2026-11-23: Session 3, Claude reviews PR #2, then Phase 1 slice (offline)

**Who:** Claude (Claude Code, cloud session). Billy said "work ahead" while setting up Spark and Ember, so this session built every part of Phase 1 that needs neither the API key nor the open answers.

**Spend delta:** **$0.00.** No live calls. Everything is tested with fake transports.

### Review of PR #2 (Spark), post-merge
PR #2 merged before review. All four fixes are correct and conservative. On the pinned toolchain (uv, CPython 3.12.3): ruff clean and 125 tests passing. One reporting nit is fixed here: `cache_savings_nusd` no longer credits savings when `cached > input`, since that case is now billed at the full rate. The review is posted on the PR.

### What changed (branch `phase-1-slice`)
- **Envelope** (`envelope.py`): .eml, PDF, image, audio and video become typed parts. Content type comes from magic bytes, never the extension, and a declared type that disagrees is kept as a signal. Email headers and attachments are parsed. A PDF's text layer (first 100 pages, as Meta does) is read with `pypdf`, so code can verify quotes. Ids derive from sha256.
- **Baselines** (`baseline.py`): vendor profiles (domains, bank accounts, usual totals), the organization and its executives. IBAN mod-97 check, account normalization, lookalike-domain detection (edit distance ≤ 2), executive impersonation, bank and amount status.
- **Schemas** (`schemas.py`): triage, invoice extraction, risk assessment and skeptic models. Every finding carries `part` plus a verbatim `quote`. `strict_json_schema` converts Pydantic into Meta's strict subset (closed objects, all fields required, nullable instead of optional).
- **Verify** (`verify.py`): quotes must appear in the named part (whitespace-normalized). Total, IBAN and invoice number must appear in the source text. Line, subtotal and total arithmetic is checked. Evidence from images or audio is marked `unverifiable`, never `pass`.
- **Policy** (`policy.py`): `Facts` (code-computed) produce a risk (LOW/MEDIUM/HIGH) with reasons, plus actions. T0 executes, T1 notifies, T2 holds, enforced in the `ActionDecision` constructor. Ember's verdict counts only with verified quotes, and it never lowers a code-set risk.
- **Actions** (`actions.py`): hold queue in SQLite (`var/holds.sqlite`). A decision needs a named human, and approving only marks the hold. Notifications go to `var/notifications.jsonl` and receipts to `var/receipts/<item>.json`, all with the secret-pattern guard.
- **Agents** (`agents/`, `ember/prompts/*.v1.md`): coordinator (triage, `minimal`), document (`low`), risk (`low`, or `medium` when code already found a red flag) and skeptic (`medium`, escalated items only). Stable instructions come first, with `prompt_cache_key` = prompt version; the item comes inside ITEM START/END as untrusted data.
- **Media input bounds:** media calls are bounded with `POST /v1/responses/input_tokens` (+10% +256). Every count is logged as a `token_count` event in `traces/budget-events.jsonl`, so reconciliation would show it if counts turn out to be billed.
- **Pipeline** (`pipeline.py`): code findings, triage, the document specialist only when there's a document, the risk specialist only when escalated, the skeptic on escalations, then verify and decide. A budget stop or invalid output still produces a conservative decision (screening incomplete means at least MEDIUM, and money actions stay held).
- **Code-level prompt-injection detection:** text addressed to automated reviewers ("ignore previous instructions", "note to AI assistant", "mark this as verified") is HIGH by code, so a skeptic can't argue it down.
- **CLI:** `forgeember screen PATH... --stage S --live [--skeptic escalated|off|always]`, `forgeember holds`, and `forgeember holds decide ID --approve|--reject --by NAME`.
- **Fixtures** (`fixturegen.py`, `scripts/gen_fixtures.py`): 13 fictional items with labels: 6 invoices (5 PDF, 1 scanned PNG) and 7 emails. They cover a bank change in an invoice, a bank change from a lookalike domain, CEO impersonation, Reply-To diversion, an inflated total, a tampered IBAN, an unknown vendor, a prompt injection, and normal and marketing mail. PDFs and emails regenerate byte for byte (tested).
- **Tests:** 172 passing (125 → 172). An "ideal Ember" fake answers like a correct model, with real quotes, and every fixture reaches its labeled category, risk and held actions end to end. Separate tests show code facts beating a benign model, a hallucinated IBAN caught by verification, a budget stop mid-item, invalid model output and API errors.

### Decisions
1. **Proposals come through structured output, not tool calls, in the slice.** Code puts its baseline facts into the prompt ("FACTS FROM OUR RECORDS"). That's cheaper (one call, no tool loop) and deterministic. Meta's `tool_choice` only supports `"auto"`, so a tool call can't be forced anyway. The read-only lookup tools stay planned for the identity specialist if evaluation shows they add accuracy.
2. **Scanned (image-only) invoices are MEDIUM risk at minimum.** Code can't verify quotes from an image. They're still held for payment as usual, with that reason shown.
3. **Skeptic rule:** if the risk specialist and the skeptic land on opposite extremes (likely_fraud vs benign), the Ember verdict becomes "suspicious". Code facts are unaffected.
4. **Dependencies:** `pypdf` (BSD) is a runtime dependency for reading text layers (not OCR: Ember reads the document, and code only checks quotes). `pillow` is a dev-only dependency, for the fixture scan.
5. **Voicemail fixtures are deferred:** no text-to-speech engine in this environment (see questions).

### Open questions for Billy (Session 1's four are still open, plus:)
5. **Voicemail fixtures:** may we add `espeak-ng` (GPL tool; the audio it produces is ours) to generate fictional voicemails, or will you record two or three short ones yourself?
6. **Scanned invoices:** OK that image-only invoices are rated MEDIUM by default, since code can't verify them?

### Next steps
1. Billy runs the smoke test, then a first live slice on two fixtures (about $0.01 worst case reserved per item):
   `uv run forgeember screen fixtures/invoices/inv-001-fennimore.pdf fixtures/emails/em-001-bank-change-lookalike.eml --stage dev --live`
2. Spark reviews the `phase-1-slice` PR.
3. Next: an evaluation harness over `fixtures/manifest.json` for Gate A (routing and field accuracy), then live prompt tuning on `dev`.

---

## 2026-11-23: Session 4, Spark post-merge review of Phase 1

**Who:** Spark (Muse Code). PR #3 merged before review, so this is a post-merge review of `main` at `cca2bc3`, per the review rule. Findings and fixes are on `spark-review-phase-1`, PR open for Claude. Billy merges. (PR #2 also merged since Session 2.)

**Spend delta:** **$0.00.** No live calls. Pinned toolchain via `uv sync --locked` (uv itself runs from scratch; the project `.venv` is local).

### Findings (all fixed on the branch)

1. **Token-count calls were unbudgeted (`client.count_input_tokens`, `agents/base.py`).** The count endpoint was called with no reservation, so if counts turn out to be billed (UNVERIFIED), that spend would be invisible to the $9 cap. Counts now reserve a static outer bound and settle as billed input; the audit event is kept. If the Usage page proves counts free, settle them at zero (one place, marked in code).
2. **Count-endpoint failure crashed screening (`agents/base.py`).** The estimator ran outside any error handling, so a 4xx/5xx from the count endpoint aborted the whole item with no decision. A failed count now falls back to the static bound with a visible error note (MEDIUM), while a budget refusal still stops without spending.
3. **Triage gated document extraction (`pipeline.py`).** A triage verdict of `normal_business` with `needs_document_extraction: false` skipped extraction entirely, so an invoice was never read. Extraction now runs whenever a document part exists, matching the documented call plan. Routine text emails still cost one call.
4. **A model-chosen category could suppress the pay hold (`pipeline.py`).** With the fix above, extraction runs, but a non-money triage category still meant no `pay` hold and no unknown-vendor/unparseable-amount reasons. A verified invoice now forces the `invoice` category; a bank-change claim from the triage route is preserved, so an invoice attached to a bank-change request holds both actions.
5. **Blind media could screen LOW and silent (`pipeline.py`).** Video (never sent to the model), audio, and unknown binaries left no signal when triage stayed quiet. Parts code cannot see now force `evidence_unverifiable` (MEDIUM + notify). PDF/image scans were already covered via extraction.
6. **A benign risk verdict erased verified triage signals (`pipeline.py`).** Only one agent's signals counted, and risk-benign dropped triage's verified quotes silently. The verdict now floors at `suspicious` when triage has signals, and verified quotes count across both agents (unverified ones stay silent, as before).
7. **Missing bank details on a money item were silent (`policy.py`).** An extraction that omits bank details produced LOW with an un-notified hold. `MISSING` on a money category is now MEDIUM.
8. **Sender-signal quotes could never verify (`envelope.py`, `agents/inputs.py`).** The model input labels a `[part: headers]` block, but no such part existed, so any quote citing it failed verification. Email headers are now a real citable part; the model input bytes are unchanged.
9. **Prompts never named the organization (`pipeline.py`).** Triage and risk couldn't spot executive requests the exact-match code check misses (nicknames, initials). Prompt facts now include the org name, domains, and executive roster.
10. **Dead always-skeptic branch (`pipeline.py`).** With no risk verdict, the skeptic's output couldn't affect the decision, and its note claimed a verdict that didn't exist. Removed the branch and the `ALWAYS` mode; Gate C can add it back with honest framing.

### Checked and cleared (no change)

- **Policy risk ratings:** every fraud pattern lands where it should; HIGH/MEDIUM all hold money via T2 regardless. The `max`-over-reasons structure means no model input can lower a code-set risk (plus a test).
- **Verification:** quote normalization is fail-closed (case-sensitive, NFKC, homoglyphs fail); total/IBAN/invoice-number in-source checks admit only values present in the source; math tolerance is ±0.01; image/audio evidence is never `pass`.
- **Skeptic combination rule:** only exact opposite extremes collapse to `suspicious`; code facts are untouched either way.
- **Envelope parsing:** magic bytes before extensions, mismatch kept as a HIGH signal, encrypted/malformed PDFs degrade to no-text-layer, HTML scripts/styles/comments excluded consistently for model and verifier.
- **Prompts:** untrusted-data framing, FACTS-trusted separation, and verbatim-quote rules are consistent across all four; no changes needed before the first live run.
- **Receipt/hold mechanics:** decided holds never reopen, decisions need a named human, approval marks only, secrets guarded on notifications and receipts.

### Left as notes for later (in the PR)

Receipt/CLI totals omit failed-call spend (ledger is complete; submission uses the ledger); EU decimal amounts misparse fail-closed; subdomain-of-vendor and exact-address-spoof senders need auth-header work; HTML link targets are dropped from model input; one unparseable file aborts a batch; notifications duplicate on re-screen; holds need multiprocess safety before parallel scale; pypdf has no time/size guard for hostile PDFs.

### Decisions

1. Assume token counts are billed until the Usage page proves otherwise (the $10 disqualification beats headline accuracy).
2. Verified document/code evidence overrides model-proposed routing (category), not just risk.
3. Unverifiable-by-code content is MEDIUM at minimum, even when the model is quiet.

### Next steps

1. Claude reviews the `spark-review-phase-1` PR; Billy merges.
2. Billy runs the smoke test, then the first live slice on two fixtures (see Session 3 next steps), then reconciles — including a check that token counts don't appear as billed usage.
3. Evaluation harness over `fixtures/manifest.json` for Gate A.

---

## 2026-11-23 (evening): first live calls, cost pass, eval harness

**Who:** Billy ran it live; Claude built. **Spend so far:** $0.0056 (14 calls, all `dev`).

- **Smoke test passed.** Auth, endpoint and model work. Reasoning tokens confirmed inside `output_tokens` on a real response.
- **First slice run:** inv-001 came out low with `pay` held, and em-001 high with `change_bank_details` held. Both match the labels.
- **Cost findings and fixes:**
  - The v1 triage prompt escalated every invoice, so a routine PDF ran risk and skeptic too: 8 calls including counts. Fixed in `triage.v2`, and the risk agent now runs only on signals, risky categories or code red flags.
  - Count calls duplicated input cost. PDFs and images now use static bounds, and audio alone is counted.
  - Risk and skeptic effort went medium → low, since the medium calls spent about 1,000 output tokens each.
- **Caching was 0%,** even for repeated triage prefixes. `scripts/cache_probe.py` measures whether a longer stable prefix caches (about $0.002).
- **`forgeember eval`** scores the 13 fixtures for Gate A (routing, fields, risk, holds), with cost per item by kind from the ledger. It writes `traces/eval/*.json`.
- **Next:** Billy runs the probe and the eval on `eval`, then we tune prompts until Gate A passes and size the scale run (Gate B) from the measured cost per item.

---

## 2026-11-23 (late): Gate A passes; scale-run tooling

**Who:** Billy ran it live; Claude built. **Spend so far:** about $0.022.

- **Gate A PASS on the 13 fixtures:** routing 92.3%, fields 100%, risk 100%, holds 100% ($0.0152). The one miss: em-006, a payment reminder, was routed `invoice` instead of `payment_request`. Its risk and held action were still right.
- **Caching works:** with a repeated prefix, 756 of 871 and 2,420 of 2,471 tokens were cached. Routine emails cost about $0.00008 each.
- **Fixture cost is attack-heavy:** escalated items cost $0.0011–0.0025, so the scale run needs a bulk mode.
- **Built:**
  - Bulk mode in `Screener`. A risk signal alone no longer buys the risk call; verified triage signals already flag the item. Extraction runs only for money items.
  - `corpus.py`: a benign-mail generator (fictional, seeded), Nazario mbox splitting, and labeled corpus loading.
  - `forgeember batch`: resumable, workers, stops at the stage limit, precision, recall and false-positive rate, cost per item and a 10K projection, written to `traces/scale/*.json`.
  - `scripts/make_corpus.py`.
- **Next (Gate B):** a 200-item pilot on `scale` to measure cost per item on the real mix, then size the full run.

---

## 2026-11-24: Gate C decided, pilot fixes

- **Gate C:** with the skeptic off, the eval stayed at 100% on routing, fields, risk and holds (em-006 also routed correctly this time), and cost fell from $0.0152 to $0.0077. The skeptic changed 0 of 13 decisions and caught no extra attacks. **Decision:** skeptic off in bulk runs; kept only to explain escalated holds in interactive screening.
- **Pilot (200 items, bulk):** precision 100% (vs synthetic benign mail), recall 97.2% on real Nazario phishing, false-positive rate 0%, $0.00043 per item. Triage was about 65% of the spend, and a phishing email cost about $0.00047 to triage, versus $0.00008 for a benign one.
- **Fixes:**
  - Legacy-charset emails were misread as binary, causing 3 of the 4 errors. Header sniffing now uses Latin-1.
  - Transient API errors (the 503) now get a second pass after 20 s.
  - Triage v3 caps signals at 3, with short quotes and explanations.
  - Bulk mode caps each text part at 6,000 characters for the model, while verification still uses the full text.
  - pypdf font warnings are silenced.
- **Next:** the full corpus run (3,459 Nazario + 3,000 benign) on `scale`, with all items screened by the same prompt version.

---

## 2026-11-24: full scale run

**Who:** Billy ran it live; Claude built. **Scale spend:** $1.74 (stage `scale`).

- **Corpus:** 6,459 emails. That's 3,459 real phishing emails (Nazario 2015–2025, CC BY 4.0) plus 3,000 fictional benign emails.
- **Result:** **6,443 screened for $1.7411, or $0.27 per 1,000. Recall 96.6% on real phishing, precision 99.7%, false-positive rate 0.3% (against synthetic benign mail).** 19 minutes with 8 workers. Projected 10K cost $2.70, so **Gate B passes** under the $4.00 rule. One byte-identical duplicate was skipped, not paid twice.
- **16 not finished:**
  - 5 had a malformed `Content-Type` (`charset=UTF-8Return-Path: …`) that raised `LookupError`. Fixed: decoding falls back to UTF-8, then Latin-1, and all local Nazario years now parse.
  - 8 were 503 `backend_unavailable`. The 20 s retry was shorter than Meta's `Retry-After: 60`. Now: 60 s, up to 2 extra passes, and only for transient errors.
  - 3 were permanent: 1 content-policy refusal on an image, 1 invalid request, 1 truncated output. Permanent errors now keep their conservative decision (≥ MEDIUM, a human looks) and are counted as `incomplete`, not retried. A truncated output gets one retry with double the output budget.
- **Next:** rerun the same command (resume screens only the 16), publish the numbers to the README, commit the traces.
- **Rerun (resume):** 8 more screened (6,451 of 6,459). 7 failed again with 503 after two 60 s passes, so they're deterministic, not transient. All 7 are phishing emails whose lure is a PDF attachment (22–130 KB) that Meta's backend can't process, while our `pypdf` reads them fine. **Fix:** if the API fails on an item with attachments, the agent is re-sent with each attachment replaced by our code's extracted text, and the rest of the item stays text-only. The item is rated at least MEDIUM: "the model could not process an attachment".

---

## 2026-11-24: scale run complete, results published

**Who:** Billy ran it; Claude verified and published.

- **Final:** 6,458 screened (3,458 unique real phishing + 3,000 synthetic benign; 1 duplicate skipped), across the full run plus two resume reruns. The last 7 were the crafted PDFs, screened through the new text fallback. Recall 96.59% (3,340 / 3,458), precision 99.73% (9 FP / 3,000), FPR 0.30%. $1.749 in calls for those items, $0.27 per 1,000, about 22 minutes with 8 workers. 3 items incomplete (permanent API errors, kept at MEDIUM or higher).
- **Ledger vs. run cost:** the ledger's `scale` stage is $2.778. That's $1.827 in successful calls (pilot included) plus $0.951 for 89 failed, rejected or incomplete calls kept at their full worst-case reservation. The 7 deterministic-503 PDFs, retried across passes, were most of it. Meta likely didn't bill the 503s, which is still UNVERIFIED.
- **Next:** reconcile with the Usage page (`forgeember budget reconcile --meta-usd <total>`). If Meta's total is near $1.86, failed calls aren't billed, and Billy decides whether the ledger should stop keeping their reservations.
- **Process:** new rule in AGENTS.md: one PR per finished piece of work.

---

## 2026-11-24: approval console and replay site

**Who:** Claude built it; Billy records the demo run.

- **`forgeember console`:** a local FastAPI + HTMX app on 127.0.0.1. It has a hold queue with Approve and Reject, which need a named person and an `HX-Request` header, and a decided hold is never reopened. Each item page shows its full decision trail, and there's a budget page. Approval only records the decision.
- **`forgeember site build`:** a static, keyless replay, built only from committed traces and fixtures. It shows headline results (aggregating full-corpus scale runs, never the pilot), a decision trail per fixture from a recorded run, Gate A, and the budget and evidence (ledger vs. Meta Usage page, stages, chart, links to the per-call trace). No JavaScript; all item text is escaped.
- **`forgeember demo export`:** copies fixture receipts into `traces/demo/`. Receipts (v2) now record the triage and risk outputs plus the email headers.
- **Dependencies:** fastapi, uvicorn, jinja2, python-multipart; htmx 1.9.12 vendored (0BSD, license file alongside).
- **Spend evidence:** Billy reports about $2.00 on Meta's Usage page against $2.81 in the ledger, consistent with the conservative charge on failed calls. It gets recorded with `budget reconcile`, which feeds the README row and the site.

---

## 2026-11-24: receipt photos at scale (CORD v2)

**Who:** Claude built it; Billy ran it.

- **Built:** `make_corpus.py cord` turns the authors' CORD v2 parquet shards (CC BY 4.0, each checked against a pinned sha256) into 1,000 JPEGs at 1280px, with labels carrying each receipt's human-labeled total. A new `receipt` route (`triage.v4`) is filed with a note to record the expense and never creates a payment. `document.v2` extracts receipts as well as invoices. Batch runs score the route and the extracted total against the labels, and the replay site aggregates runs per corpus.
- **Gate A re-run** on the new prompts: 100% routing, fields, risk and holds, $0.0155.
- **Result:** 998 photos screened (2 exact duplicates skipped), 0 errors, about 7 minutes with 8 workers. **Totals 99.0% correct (960 / 970 labeled), 99.5% routed as receipts, 5 (0.5%) read as invoices and held for a person. $0.759, or $0.76 per 1,000 photos.** 2 calls per photo, each about 2,000–2,300 input tokens with the image (max 2,711), so the static 6,000-token image bound held with no overruns.
- **Spend:** ledger total $3.5968. The `scale` stage has $0.463 left.
- **Next:** the voicemail run, then the identity (tool-calling) run, each in its own PR.

---

## 2026-11-24: voicemails, live

**Who:** Claude built it; Billy ran it.

- **Built:** a voicemail specialist (`voicemail.v1`) on every audio part: transcript, caller, callback number, request, money asked for, bank-change claim, account read out. Code compares an account read out with the vendor's account on file, and treats a spoken money or bank request as unconfirmed (at least MEDIUM, always held). A caller using the owner's name to ask for money is HIGH. 4 fictional voicemails (espeak-ng), one delivered as a phone-system email with a WAV attached.
- **First live audio on Ember: it works.** All 4 voicemails got the right risk and holds in every run (up to 2 screens and 2 Gate A evals each). Callback numbers and the amount were heard right.
- **What it misheard:** "Marrowvale" as "Marrow Vale", "Kestrel Byte Hosting" as "Kestrel Bite Posting" (3 of 4 runs), and the bank digits read out by the robot voice (the account number wrong in every run, the sort code right once). The first run therefore couldn't find the vendor, so the account check never fired. vm-002 stayed HIGH on the bank-change claim alone.
- **Fix:** company names heard in a recording are matched by sound: spaces ignored, and a single vendor within 2 letters (8+ characters). Voice only; written names stay exact. Re-run: vm-002 now also reports "bank account differs from every account on file".
- **Gate A (17 fixtures):** risk and holds 100%, routing 94.1% (em-006 routed invoice vs. payment request, payment held either way), fields 93.8%, $0.0138.
- **Spend:** ledger total $3.6348.
- **Next:** the identity (tool-calling) run on `identity-tools` (PR #16, draft until this merges).

---

## 2026-11-25: tool calling, live

**Who:** Claude built it; Billy ran it.

- **Built:** an identity specialist (`identity.v1`) with function calling. Ember asks for read-only `lookup_vendor` calls; code runs them and returns names, domains and usual amounts, never bank details. Each round is its own budgeted ledger call (at most 3 rounds, 4 lookups per round). A vendor-claim mismatch (HIGH) needs the claim quoted verbatim, the vendor id returned by a lookup in that conversation, and a sender domain not on file for that vendor. It runs only on emails asking for money or a bank change from a sender code doesn't know, never in bulk mode. New fixture em-008.
- **First live tool calls on Ember: they work, with strict structured output in the same request.** em-008: one lookup, then the answer. Code verified all three conditions, so the result was HIGH with the payment held (MEDIUM without it). em-007: Ember looked up the name, then the domain on its own; both not found, so no claim.
- **Gate A (18 fixtures):** risk and holds 100%, routing 94.4% (em-008 routed invoice vs. payment request, payment held either way), fields 93.8%, $0.0178.
- **Process slip, fixed:** a `git pull` on Billy's machine didn't update `identity-tools`, so the run's trace lines were appended to an older trace file and the ledger check refused to publish. The 78 call lines and 12 event lines were saved, re-applied on top of `main` (PR #16 had merged in the meantime) and published from the new branch `identity-results`; the ledger and trace agree.
- **Spend:** ledger total $3.6563 of the $9.00 cap.

---

## 2026-11-25: submission package, part 1

**Who:** Claude.

- **README for judges:** the Meta tooling table now gives live figures instead of "pending" (10,249 calls, six strict schemas, 45.6% cached, tool calling and audio run live). The unbuilt 1M-context row is removed. The architecture diagram (README and ARCHITECTURE.md) matches the agents that exist. A "Try it without a key" row points to the replay site, and Gate A leads with the 18-fixture result.
- **Replay site:** shows the newest Gate A report (18 fixtures), not the Nov 23 one, and its "How it works" covers voicemails and the lookup tool.
- **`docs/SUBMISSION.md`:** form text and video script filled with real numbers. Tool calling is added to the description, the Meta tooling list and video segment 4. The only value left is the final total spent, read on Dec 1.
- **`scripts/check_secrets.py`:** scans files before they're committed or submitted (above all the Muse Code trajectory) and masks what it finds.
- **Next (Dec 1, Billy):** video, Usage-page screenshot, reconcile, trajectory export; then the final PR.

