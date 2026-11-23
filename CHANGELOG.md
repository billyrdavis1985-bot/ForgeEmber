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
- Cache-savings report no longer credits savings on malformed usage billed at the full rate.

### Added
- Phase 0 foundation (2026-11-23):
  - Docs: intent, architecture ADR, plan with decision gates, hackathon rules checklist, budget design, data register, verified Meta API notes, build log.
  - Budget module: Muse Ember price table in exact integer nano-dollars (both context tiers), reserve-then-settle ledger (SQLite WAL + append-only JSONL trace), stage sub-budgets and a $9.00 global hard cap, approved stage transfers, reconciliation against Meta's Usage page, crash recovery for orphaned reservations, and credential-pattern guard on every trace line.
  - `forgeember budget` CLI: report, `--json`, `reconcile`, `transfer`, `expire-open`, `publish`.
  - README "Budget & Trace" block, snapshot and SVG chart, generated from the ledger and checked in CI.
  - Budgeted Muse Ember Responses API client (the only network module). Not yet run live.
  - `scripts/smoke_test.py` (requires `--live`), `render_budget_readme.py`, `export_trace.py`, `gen_fixtures.py` stub.
  - CI (ruff + pytest), Dockerfile, MIT license.
