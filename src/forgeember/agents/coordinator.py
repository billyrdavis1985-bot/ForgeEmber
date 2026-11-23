"""Coordinator (triage): classifies the item, flags manipulation and decides whether to escalate.

Runs on every item at the lowest effort. It's the bulk path, so its cost sets the per-item floor.
"""

from __future__ import annotations

from forgeember.agents.base import AgentSpec
from forgeember.ember.prompts import load_prompt
from forgeember.schemas import TriageResult

TRIAGE = AgentSpec(
    role="coordinator",
    prompt=load_prompt("triage", 2),
    model=TriageResult,
    schema_name="triage_result",
    effort="minimal",
    max_output_tokens=2048,
)
