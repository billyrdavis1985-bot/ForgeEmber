"""The sole decision authority. Ember proposes; this module decides (Phase 1).

Tier table (an invariant, see docs/INTENT.md):
  T0 automatic: file, tag, route a note
  T1 notify the operator
  T2 HOLD for human approval: payments, bank-detail changes, outside sends, deletions
Ember can never execute a T2 action. Unknown action kinds default to T2.
"""

from __future__ import annotations

from enum import StrEnum


class Tier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"


ACTION_TIERS: dict[str, Tier] = {
    "file": Tier.T0,
    "tag": Tier.T0,
    "route_note": Tier.T0,
    "notify_operator": Tier.T1,
    "pay": Tier.T2,
    "change_bank_details": Tier.T2,
    "send_external": Tier.T2,
    "delete": Tier.T2,
}


def tier_for(action_kind: str) -> Tier:
    """Unknown actions are treated as the riskiest tier."""
    return ACTION_TIERS.get(action_kind, Tier.T2)


def decide(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError("Phase 1: apply tiers to verified findings")
