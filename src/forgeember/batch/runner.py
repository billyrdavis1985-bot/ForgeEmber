"""Resumable, rate-limited, budget-capped scale runs (Phase 2).

Honors x-ratelimit-* headers and Retry-After. Every call goes through the ledger, so the run
stops at the scale stage limit instead of overspending.
"""
