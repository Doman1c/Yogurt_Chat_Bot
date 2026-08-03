from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StringEnum(str, Enum):
    """Python 3.9-compatible equivalent of enum.StrEnum."""

    def __str__(self) -> str:
        return self.value


class Scene(StringEnum):
    TECHNICAL = "technical"
    CASUAL = "casual"
    PLAYFUL = "playful"
    COMFORT = "comfort"
    CELEBRATION = "celebration"
    SERIOUS = "serious"


class Mood(StringEnum):
    NEUTRAL = "neutral"
    CURIOUS = "curious"
    HAPPY = "happy"
    SAD = "sad"
    FRUSTRATED = "frustrated"
    ANXIOUS = "anxious"
    SPEECHLESS = "speechless"
    EMBARRASSED = "embarrassed"
    AFFECTIONATE = "affectionate"
    ANGRY = "angry"
    SURPRISED = "surprised"
    CONFUSED = "confused"
    TIRED = "tired"
    PROUD = "proud"
    WARM = "warm"


class ReplyLength(StringEnum):
    SHORT = "short"
    MEDIUM = "medium"
    DETAILED = "detailed"


class Relationship(StringEnum):
    NEW = "new"
    FAMILIAR = "familiar"
    CLOSE = "close"
    OWNER = "owner"


@dataclass
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


@dataclass
class MemorySnippet:
    kind: str
    text: str
    score: float
    source_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
