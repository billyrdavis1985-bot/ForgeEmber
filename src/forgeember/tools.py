"""Read-only lookup tools exposed to Ember via function calling (Phase 1).

lookup_vendor, lookup_sender_history and search_notes read local baselines only. No tool
here can change state, send anything or move money. Proposed actions are separate
function calls that ``policy.py`` intercepts.
"""
