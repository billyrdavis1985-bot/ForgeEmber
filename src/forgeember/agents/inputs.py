"""Build the Responses API ``input`` for one item.

Layout, chosen for prompt caching: the stable prefix is the agent's ``instructions``, sent
separately and identical across items. The item follows inside ITEM START / ITEM END, then the
facts code computed, then any per-agent note (such as the skeptic's "other verdict").
Every part is labeled with the exact name agents must cite in evidence.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

from forgeember.agents.base import data_url
from forgeember.budget.ledger import InputKind
from forgeember.envelope import Envelope, Part, render_headers


def _text(text: str) -> dict[str, Any]:
    return {"type": "input_text", "text": text}


def _media_block(part: Part) -> dict[str, Any] | None:
    if part.kind is InputKind.PDF:
        return {
            "type": "input_file",
            "filename": part.filename or "document.pdf",
            "file_data": data_url(part.mime, part.data),
        }
    if part.kind is InputKind.IMAGE:
        return {"type": "input_image", "image_url": data_url(part.mime, part.data)}
    if part.kind is InputKind.AUDIO:
        fmt = "wav" if part.mime == "audio/wav" else "mp3"
        return {
            "type": "input_audio",
            "input_audio": {"data": base64.b64encode(part.data).decode("ascii"), "format": fmt},
        }
    return None  # video is the Gate D stretch; unknown binaries are never sent


def _cap(text: str, max_chars: int | None) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[... {len(text) - max_chars} more characters not shown]"


def item_content(
    env: Envelope,
    facts: Sequence[str] = (),
    note: str | None = None,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [_text("ITEM START")]
    header_part = env.part("headers")
    if header_part is not None and header_part.text is not None:
        blocks.append(_text(f"[part: headers]\n{header_part.text}"))
    elif env.headers is not None:  # hand-built envelope without a headers part
        blocks.append(_text(f"[part: headers]\n{render_headers(env.headers)}"))
    for part in env.parts:
        label = f"[part: {part.name}] ({part.mime})"
        if part.kind in (InputKind.TEXT, InputKind.EMAIL) and part.text is not None:
            blocks.append(_text(f"{label}\n{_cap(part.text, max_chars)}"))
            continue
        media = _media_block(part)
        if media is None:
            blocks.append(_text(f"{label}\n(content not sent: unsupported type)"))
        else:
            blocks.append(_text(label))
            blocks.append(media)
    blocks.append(_text("ITEM END"))
    if facts:
        blocks.append(
            _text(
                "FACTS FROM OUR RECORDS (computed by our code):\n"
                + "\n".join(f"- {f}" for f in facts)
            )
        )
    if note:
        blocks.append(_text(note))
    return [{"role": "user", "content": blocks}]


def primary_kind(env: Envelope) -> InputKind:
    """The input kind recorded in the ledger for calls about this item."""
    if env.kind is InputKind.EMAIL:
        return InputKind.EMAIL
    return env.parts[0].kind if env.parts else env.kind
