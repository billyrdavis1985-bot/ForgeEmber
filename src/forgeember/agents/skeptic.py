"""Skeptic: argues the opposite conclusion on escalated cases (Gate C decides if it stays)."""

from __future__ import annotations

from forgeember.agents.base import AgentSpec
from forgeember.ember.prompts import load_prompt
from forgeember.schemas import SkepticView

SKEPTIC = AgentSpec(
    role="skeptic",
    prompt=load_prompt("skeptic", 1),
    model=SkepticView,
    schema_name="skeptic_view",
    effort="medium",
    max_output_tokens=2048,
)


def skeptic_note(verdict: str) -> str:
    return f"THE OTHER REVIEWER'S VERDICT: {verdict}"
