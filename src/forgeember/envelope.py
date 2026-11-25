"""Envelope: the normalized wrapper every input gets before any agent sees it.

An Envelope is built only from bytes we already hold. It never fetches anything.

* The type comes from magic bytes, not from the file extension or a declared MIME type.
  A declared type that disagrees with the bytes is kept as a risk signal.
* ``Part.text`` is text that *code* can verify quotes against: email headers and body,
  or a PDF's text layer (first 100 pages, matching Meta's PDF handling). Images, audio
  and video have no text, so evidence quoted from them is "unverifiable by code".
* The id is derived from the sha256 of the raw bytes, so the same input always gets the same id.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from forgeember.budget.ledger import InputKind

MAX_INLINE_BYTES = 50_000_000  # Meta's inline file limit
PDF_TEXT_PAGES = 100  # Meta extracts text from the first 100 pages

_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def sniff_mime(data: bytes) -> str:
    """Identify content by magic bytes. Unknown binary -> application/octet-stream."""
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    head_text = data[:4096].decode("latin-1")  # never fails; RFC 822 headers are ASCII
    if re.search(r"(?im)^(from|subject|message-id|mime-version|return-path):", head_text):
        return "message/rfc822"  # bodies in legacy charsets (Latin-1 etc.) are still email
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"


def kind_for_mime(mime: str) -> InputKind | None:
    if mime == "application/pdf":
        return InputKind.PDF
    if mime in _IMAGE_MIMES:
        return InputKind.IMAGE
    if mime in ("audio/wav", "audio/mpeg"):
        return InputKind.AUDIO
    if mime == "video/mp4":
        return InputKind.VIDEO
    if mime == "message/rfc822":
        return InputKind.EMAIL
    if mime == "text/plain":
        return InputKind.TEXT
    return None


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()


def pdf_text(data: bytes) -> str | None:
    """Text layer of a PDF (first 100 pages). None if unreadable or image-only."""
    try:
        import logging

        from pypdf import PdfReader

        logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings on hostile PDFs

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return None
        pages = [p.extract_text() or "" for p in reader.pages[:PDF_TEXT_PAGES]]
    except Exception:  # malformed PDFs are common in the wild; treat as no text layer
        return None
    text = "\n".join(pages).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class Part:
    name: str  # "body", "attachment:<filename>", or a neutral name for a bare file ("document.pdf")
    mime: str  # sniffed from bytes
    kind: InputKind
    sha256: str
    data: bytes = field(repr=False)
    text: str | None  # verifiable text (email body, PDF text layer); None for media
    declared_mime: str | None = None
    filename: str | None = None

    @property
    def mime_mismatch(self) -> bool:
        """Declared type disagrees with the bytes (e.g. an executable named invoice.pdf)."""
        if self.declared_mime is None or self.declared_mime == "application/octet-stream":
            return False
        return self.declared_mime.split(";")[0].strip().lower() != self.mime


@dataclass(frozen=True, slots=True)
class EmailHeaders:
    from_name: str
    from_addr: str
    reply_to: str | None
    to: tuple[str, ...]
    subject: str
    date: str | None
    message_id: str | None

    @property
    def from_domain(self) -> str:
        return self.from_addr.rpartition("@")[2].lower()

    @property
    def reply_to_domain(self) -> str | None:
        return self.reply_to.rpartition("@")[2].lower() if self.reply_to else None


def render_headers(h: EmailHeaders) -> str:
    """The header block agents see (and cite quotes from) as part "headers"."""
    return (
        f"From: {h.from_name} <{h.from_addr}>\n"
        f"Reply-To: {h.reply_to or '(none)'}\n"
        f"To: {', '.join(h.to) or '(none)'}\n"
        f"Subject: {h.subject}\n"
        f"Date: {h.date or '(none)'}"
    )


@dataclass(frozen=True, slots=True)
class Envelope:
    id: str
    sha256: str
    source: str
    kind: InputKind
    mime: str
    size_bytes: int
    received_at: datetime | None
    parts: tuple[Part, ...]
    headers: EmailHeaders | None = None

    def part(self, name: str) -> Part | None:
        return next((p for p in self.parts if p.name == name), None)

    @property
    def media_parts(self) -> tuple[Part, ...]:
        return tuple(p for p in self.parts if p.kind not in (InputKind.EMAIL, InputKind.TEXT))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_part(
    name: str, data: bytes, *, declared: str | None = None, filename: str | None = None
) -> Part:
    if len(data) > MAX_INLINE_BYTES:
        raise ValueError(f"part {name!r} is {len(data):,} bytes; inline limit is 50 MB")
    mime = sniff_mime(data)
    kind = kind_for_mime(mime)
    if kind is None:
        kind = InputKind.TEXT
        text = None
    elif kind is InputKind.PDF:
        text = pdf_text(data)
    elif kind in (InputKind.TEXT, InputKind.EMAIL):
        text = data.decode("utf-8", errors="replace")
    else:
        text = None
    return Part(
        name=name,
        mime=mime,
        kind=kind,
        sha256=_sha(data),
        data=data,
        text=text,
        declared_mime=declared,
        filename=filename,
    )


def _text_content(part: EmailMessage) -> str:
    """Decoded text of a MIME part, tolerating broken charset parameters.

    Real phishing often has malformed headers (e.g. ``charset=UTF-8Return-Path: ...`` when a
    line break is missing). Python then raises LookupError. Fall back to UTF-8, then Latin-1.
    """
    try:
        return part.get_content()
    except (LookupError, UnicodeError, ValueError):
        payload = part.get_payload(decode=True) or b""
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return payload.decode("latin-1")


def _parse_email(data: bytes) -> tuple[EmailHeaders, tuple[Part, ...], datetime | None]:
    msg: EmailMessage = email.message_from_bytes(data, policy=email.policy.default)  # type: ignore[assignment]
    from_name, from_addr = parseaddr(str(msg.get("From", "")))
    reply = parseaddr(str(msg.get("Reply-To", "")))[1] or None
    to = tuple(a for _, a in getaddresses([str(v) for v in msg.get_all("To", [])]) if a)
    date_raw = msg.get("Date")
    received = None
    if date_raw:
        try:
            received = parsedate_to_datetime(str(date_raw))
        except (TypeError, ValueError):
            received = None
    headers = EmailHeaders(
        from_name=from_name,
        from_addr=from_addr.lower(),
        reply_to=reply.lower() if reply else None,
        to=to,
        subject=str(msg.get("Subject", "")),
        date=str(date_raw) if date_raw else None,
        message_id=str(msg.get("Message-ID")) if msg.get("Message-ID") else None,
    )
    parts: list[Part] = []
    header_text = render_headers(headers)
    header_bytes = header_text.encode("utf-8")
    parts.append(
        Part(
            name="headers",
            mime="text/plain",
            kind=InputKind.TEXT,
            sha256=_sha(header_bytes),
            data=header_bytes,
            text=header_text,
        )
    )
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is not None:
        content = _text_content(body)
        text = html_to_text(content) if body.get_content_type() == "text/html" else content
        body_bytes = text.encode("utf-8")
        parts.append(
            Part(
                name="body",
                mime="text/plain",
                kind=InputKind.TEXT,
                sha256=_sha(body_bytes),
                data=body_bytes,
                text=text,
            )
        )
    for att in msg.iter_attachments():
        payload = att.get_payload(decode=True) or b""
        filename = att.get_filename() or "attachment"
        parts.append(
            _make_part(
                f"attachment:{filename}",
                payload,
                declared=att.get_content_type(),
                filename=filename,
            )
        )
    return headers, tuple(parts), received


# What a bare file is called in model input. The operator's file name stays in
# ``Envelope.source`` for people and receipts, and never reaches the model: a name like
# "inv-006-marrowvale-bad-iban.pdf" would hint at the answer. (Attachment names inside an
# email are part of the message, so those are kept.)
_NEUTRAL_STEM = {
    InputKind.PDF: "document",
    InputKind.IMAGE: "image",
    InputKind.AUDIO: "recording",
    InputKind.VIDEO: "video",
    InputKind.TEXT: "document",
}
_NEUTRAL_EXT = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
    "text/plain": ".txt",
}


def neutral_name(kind: InputKind, mime: str) -> str:
    return _NEUTRAL_STEM.get(kind, "file") + _NEUTRAL_EXT.get(mime, "")


def from_bytes(data: bytes, *, source: str, filename: str | None = None) -> Envelope:
    """``filename`` is accepted for callers' convenience but is never part of the model
    input for a bare file: see ``neutral_name``."""
    digest = _sha(data)
    mime = sniff_mime(data)
    kind = kind_for_mime(mime)
    if kind is None:
        raise ValueError(f"{source}: unsupported content type {mime}")
    headers = None
    received = None
    if kind is InputKind.EMAIL:
        headers, parts, received = _parse_email(data)
    else:
        parts = (_make_part(neutral_name(kind, mime), data),)
    return Envelope(
        id=f"itm_{digest[:24]}",
        sha256=digest,
        source=source,
        kind=kind,
        mime=mime,
        size_bytes=len(data),
        received_at=received,
        parts=parts,
        headers=headers,
    )


def from_path(path: str | Path) -> Envelope:
    p = Path(path)
    return from_bytes(p.read_bytes(), source=str(p), filename=p.name)
