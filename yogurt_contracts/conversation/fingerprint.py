"""Versioned deterministic canonicalization for Turn retries."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Dict

from ..foundation.idempotency import RequestFingerprint
from ..foundation.model import FrozenContract
from .input import MediaReferencePart, PerceptionReferencePart, TextInputPart, ToolResultInputPart, TurnInput
from .refs import TurnOrigin


class TurnFingerprint(FrozenContract):
    fingerprint_version: int = 1
    input_digest: RequestFingerprint
    origin_digest: RequestFingerprint
    principal_binding_digest: RequestFingerprint


def _digest(value: object) -> RequestFingerprint:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return RequestFingerprint(value=hashlib.sha256(encoded).hexdigest())


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def _part_value(part: object) -> Dict[str, Any]:
    if isinstance(part, TextInputPart):
        return {"type": "text", "text": _normalize_text(part.text)}
    if isinstance(part, PerceptionReferencePart):
        return {"type": "perception_reference", "reference": part.perception_reference}
    if isinstance(part, MediaReferencePart):
        return {"type": "media_reference", "reference": part.media_reference}
    if isinstance(part, ToolResultInputPart):
        return {"type": "tool_result_reference", "reference": part.tool_result_reference}
    raise TypeError("unsupported TurnInput part")


def _origin_value(origin: TurnOrigin) -> Dict[str, str]:
    return {"surface": origin.origin_surface, "principal": origin.external_principal_ref.value, "resolution": origin.identity_resolution_ref.value}


def turn_fingerprint(turn_input: TurnInput, principal_binding: str) -> TurnFingerprint:
    """v1: UTF-8, NFC, normalized line endings, exact other whitespace, SHA-256."""
    origin = _origin_value(turn_input.origin)
    return TurnFingerprint(input_digest=_digest({"parts": [_part_value(part) for part in turn_input.parts]}), origin_digest=_digest(origin), principal_binding_digest=_digest({"principal_binding": principal_binding}))
