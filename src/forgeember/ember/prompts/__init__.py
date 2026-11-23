"""Versioned prompt files (``<name>.v<N>.md``). Each prompt's version and sha256 go into the trace.

A prompt file is never edited once it has been used in a recorded run. Add ``<name>.v<N+1>.md``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str  # e.g. "triage.v1"; recorded as prompt_version in the trace
    text: str
    sha256: str


@cache
def load_prompt(name: str, version: int) -> Prompt:
    path = PROMPT_DIR / f"{name}.v{version}.md"
    text = path.read_text(encoding="utf-8")
    return Prompt(
        name=name,
        version=f"{name}.v{version}",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
