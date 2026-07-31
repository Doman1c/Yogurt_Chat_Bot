from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Scene(StrEnum):
    TECHNICAL = "technical"
    CASUAL = "casual"
    PLAYFUL = "playful"
    COMFORT = "comfort"
    CELEBRATION = "celebration"
    SERIOUS = "serious"


class Mood(StrEnum):
    NEUTRAL = "neutral"
    CURIOUS = "curious"
    HAPPY = "happy"
    SAD = "sad"
    FRUSTRATED = "frustrated"
    ANXIOUS = "anxious"


class ReplyLength(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    DETAILED = "detailed"


class Relationship(StrEnum):
    NEW = "new"
    FAMILIAR = "familiar"
    CLOSE = "close"
    OWNER = "owner"


@dataclass(slots=True)
class DialogueState:
    scene: Scene
    mood: Mood
    relationship: Relationship
    reply_style: str
    reply_length: ReplyLength
    humor_level: float
    use_memory: bool
    use_sticker: bool
    response_rules: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemorySnippet:
    kind: str
    text: str
    score: float
    source_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
