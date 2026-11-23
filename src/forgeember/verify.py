"""Deterministic verification of Ember findings (Phase 1).

Checks: schema validity, cited quotes exist verbatim in the source, invoice arithmetic
(line items, tax, total), and baseline matches (known bank account, usual amount, usual
sender). A finding that fails verification never reaches policy as trusted evidence.
"""
