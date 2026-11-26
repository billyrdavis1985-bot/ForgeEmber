"""Shared rendering for the approval console and the static replay site.

Both render the same receipt template, so the console and the public replay show the same
decision trail. Jinja autoescaping is on: item content is untrusted (emails, PDFs), and it's
always shown as text, never as markup.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from forgeember.budget.pricing import fmt_usd
from forgeember.envelope import Envelope

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_INPUT_CHARS = 20_000


def check_status(receipt: dict[str, Any], prefix: str) -> str | None:
    """Status of the first verification check whose name starts with ``prefix``."""
    for check in receipt.get("checks") or []:
        if check["name"].startswith(prefix):
            return check["status"]
    return None


def _usd(nusd: int | None) -> str:
    return "n/a" if nusd is None else fmt_usd(int(nusd))


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def jinja_env() -> Environment:
    env = Environment(
        loader=PackageLoader("forgeember", "web/templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["usd"] = _usd
    env.filters["pct"] = _pct
    env.globals["check_status"] = check_status
    return env


def input_parts(
    env: Envelope | None, *, file_urls: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """What the item looked like, for display. Headers are shown from the receipt instead."""
    if env is None:
        return []
    out = []
    for part in env.parts:
        if part.name == "headers":
            continue
        view: dict[str, Any] = {
            "name": part.name,
            "mime": part.mime,
            "text": None,
            "image_url": None,
            "audio_url": None,
            "video_url": None,
            "file_url": (file_urls or {}).get(part.name),
        }
        if part.text:
            text = part.text
            if len(text) > MAX_INPUT_CHARS:
                text = (
                    text[:MAX_INPUT_CHARS]
                    + f"\n[... {len(part.text) - MAX_INPUT_CHARS} more characters]"
                )
            view["text"] = text
        if part.mime.startswith(("image/", "audio/", "video/")):
            key = part.mime.split("/")[0] + "_url"
            view[key] = f"data:{part.mime};base64,{base64.b64encode(part.data).decode('ascii')}"
        out.append(view)
    return out
