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
