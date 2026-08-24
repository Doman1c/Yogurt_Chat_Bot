from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


Language = Literal["zh", "ja"]
Emotion = Literal[
    "neutral",
    "happy",
    "soft",
    "shy",
    "sad",
    "angry",
    "surprised",
]


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: Literal["yogurt"] = "yogurt"
    language: Language = "zh"
    emotion: Emotion = "neutral"
    intensity: float = Field(default=0.7, ge=0.0, le=1.0)
    speed: Optional[float] = Field(default=None, ge=0.80, le=1.30)
    stream: bool = False

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value

    @field_validator("stream")
    @classmethod
    def streaming_not_enabled_yet(cls, value: bool) -> bool:
        if value:
            raise ValueError("streaming is not enabled in P10-A3")
        return value


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    runtime: Literal["ready"] = "ready"
    engine: Literal["gpt_sovits"] = "gpt_sovits"
    engine_reachable: bool
    default_profile: str
    profiles: list[str]
