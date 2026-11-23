"""Static configuration: API constants and repository paths.

No secrets live here. The API key is read only by ``forgeember.ember.client`` at call time,
from the ``MODEL_API_KEY`` environment variable, and is never stored, logged or printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Verified 2026-11-23 against https://dev.meta.ai/docs/muse-ember and
# https://dev.meta.ai/docs/authentication (see docs/META_API_NOTES.md).
API_BASE_URL = "https://api.meta.ai/v1"
EMBER_MODEL = "muse-ember-1.0"
API_KEY_ENV = "MODEL_API_KEY"


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path

    @property
    def ledger_db(self) -> Path:
        return self.root / "var" / "ledger.sqlite"  # gitignored, local source of truth

    @property
    def calls_trace(self) -> Path:
        return self.root / "traces" / "calls.jsonl"  # committed, append-only mirror

    @property
    def events_trace(self) -> Path:
        return self.root / "traces" / "budget-events.jsonl"

    @property
    def snapshot(self) -> Path:
        return self.root / "traces" / "budget-snapshot.json"

    @property
    def submission_trace(self) -> Path:
        return self.root / "traces" / "submission-trace.jsonl"

    @property
    def submission_summary(self) -> Path:
        return self.root / "traces" / "summary.json"

    @property
    def readme(self) -> Path:
        return self.root / "README.md"

    @property
    def chart(self) -> Path:
        return self.root / "docs" / "assets" / "budget-chart.svg"

    @property
    def holds_db(self) -> Path:
        return self.root / "var" / "holds.sqlite"

    @property
    def receipts_dir(self) -> Path:
        return self.root / "var" / "receipts"

    @property
    def notifications(self) -> Path:
        return self.root / "var" / "notifications.jsonl"

    @property
    def baselines(self) -> Path:
        return self.root / "fixtures" / "baselines.json"


def find_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: cwd) to the directory holding ForgeEmber's pyproject."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and 'name = "forgeember"' in pyproject.read_text(encoding="utf-8"):
            return candidate
    return here
