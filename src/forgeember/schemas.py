"""Pydantic v2 models for every Ember output (Phase 1).

Each model doubles as the JSON schema sent as ``text.format`` (strict, root object,
``additionalProperties: false``) and as the validator for the response. Every finding must
cite evidence as exact quotes or byte spans from the source, which ``verify.py`` checks.
"""
