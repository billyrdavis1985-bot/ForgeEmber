"""Envelope: the normalized wrapper every input gets before any agent sees it.

Contract (Phase 1): ``Envelope`` carries id, sha256 of the raw bytes, source, mime type,
input kind (email/pdf/image/audio/video), received_at, and a pointer to the raw bytes. It's
built only from bytes we hold; it never fetches anything.
"""

from __future__ import annotations


def from_path(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError("Phase 1: normalize .eml/PDF/image/audio/video into an Envelope")
