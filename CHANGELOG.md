# Changelog

All notable changes to ForgeEmber. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added (Phase 1 slice, 2026-11-23)
- Envelope (magic-byte sniffing, email and attachment parsing, PDF text layer), vendor and executive baselines (IBAN mod-97, lookalike domains, impersonation), strict Ember output schemas, quote and arithmetic verification, a T0/T1/T2 policy with risk reasons, a hold queue, receipts and notifications.
- Ember agents (coordinator, document, risk, skeptic) with versioned prompts, and the screening pipeline with a conservative decision on budget stops or invalid output.
- Code-level prompt-injection detection.
- Token-count endpoint for media input bounds, audit-logged.
- `forgeember screen` and `forgeember holds` commands.
- 13 fictional labeled fixtures with a deterministic generator.

### Fixed
- A payment request that triage misfiles as ordinary mail keeps its payment hold (2026-11-28). Code reads the text too (`moneytext.py`): an IBAN, a labelled account number, or a request to pay an amount or an invoice keeps the message on a money route, and account details said to be new add the bank-change hold. Raise-only. Before, all 9 email-only money fixtures lost their hold under a wrong route (36 of 120 misfilings); now none does.
- Cache-savings report no longer credits savings on malformed usage billed at the full rate.
- Post-merge review of Phase 1 (Spark): the token-count call is reserved and settled like any other call; document extraction runs whenever a document part exists; a verified invoice forces the invoice category (a triage bank-change claim is preserved); media code cannot see is MEDIUM at minimum; a benign risk verdict no longer erases verified triage signals; missing bank details on a money item is MEDIUM; prompt facts name the organization and executives; email headers are a citable part; the dead always-skeptic branch is removed.
- Post-merge review of the hold workflow, attack suite and duplicate guard (Spark): a re-sent invoice that is itself suspect keeps both warnings on the follow-up link; invisible-character handling splits matching (broad: marks, selectors, tags) from flagging (narrow: joiners no longer flag, so emoji and Indic text stay quiet); the PDF hidden-text scan also catches hex-string text.

### Added
- Usage-trajectory JSON export (`scripts/export_usage_trajectory.py` → `traces/usage-trajectory.json`): totals, daily buckets and a per-call cumulative spend series from the committed call trace.
- Phase 0 foundation (2026-11-23):
  - Docs: intent, architecture ADR, plan with decision gates, hackathon rules checklist, budget design, data register, verified Meta API notes, build log.
  - Budget module: Muse Ember price table in exact integer nano-dollars (both context tiers), reserve-then-settle ledger (SQLite WAL + append-only JSONL trace), stage sub-budgets and a $9.00 global hard cap, approved stage transfers, reconciliation against Meta's Usage page, crash recovery for orphaned reservations, and credential-pattern guard on every trace line.
  - `forgeember budget` CLI: report, `--json`, `reconcile`, `transfer`, `expire-open`, `publish`.
  - README "Budget & Trace" block, snapshot and SVG chart, generated from the ledger and checked in CI.
  - Budgeted Muse Ember Responses API client (the only network module). Not yet run live.
  - `scripts/smoke_test.py` (requires `--live`), `render_budget_readme.py`, `export_trace.py`, `gen_fixtures.py` stub.
  - CI (ruff + pytest), Dockerfile, MIT license.
