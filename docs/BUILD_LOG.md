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
