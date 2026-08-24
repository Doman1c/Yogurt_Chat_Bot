from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from voice_orchestrator.schemas import Scene
from voice_runtime.schemas import Language


MouthErrorCode = Literal[
    "invalid_request",
    "mouth_unavailable",
    "voice_runtime_unreachable",
    "synthesis_failed",
    "timeout",
]


class SpeakRequest(BaseModel):
    """A bounded, backend-agnostic request to synthesize Yogurt speech."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    scene: Scene = "casual"
    language: Language = "zh"

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class SpeakResult(BaseModel):
    """Bounded result of a Mouth synthesis request.

    Audio is returned in memory for the caller's chosen playback or transport
    layer. Mouth does not persist it and intentionally exposes no raw backend
    error details.
    """

    success: bool
    audio: Optional[bytes] = None
    media_type: Optional[str] = None
    scene: Scene
    voice: Literal["yogurt"] = "yogurt"
    language: Language
    latency_ms: Optional[int] = Field(default=None, ge=0)
    error_code: Optional[MouthErrorCode] = None
