from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from voice_runtime.schemas import Emotion, Language


Scene = Literal[
    "casual",
    "technical",
    "playful",
    "comfort",
    "celebration",
    "serious",
]


class VoicePlanInput(BaseModel):
    text: str = Field(min_length=1)
    language: Language = "zh"
    scene: Scene = "casual"
    mood: Optional[Emotion] = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value
