from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class ContextRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ContextEntry:
    role: ContextRole
    text: str


@dataclass(frozen=True)
class ContextProjection:
    entries: Tuple[ContextEntry, ...]
    conversation_metadata: str
