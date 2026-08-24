from __future__ import annotations

from enum import Enum, IntEnum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SpeechPriority(IntEnum):
    LOW = 1
    NORMAL = 2
    HIGH = 3


class InterruptionPolicy(str, Enum):
    NEVER = "never"
    HIGHER_PRIORITY = "higher_priority"
    ALWAYS = "always"


class VoiceSessionState(str, Enum):
    IDLE = "idle"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"


class UtteranceState(str, Enum):
    PENDING = "pending"
    SYNTHESIZING = "synthesizing"
    READY = "ready"
    PLAYING = "playing"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


VoiceSessionErrorCode = Literal[
    "queue_full",
    "unknown_sink",
    "invalid_request",
    "voice_runtime_unreachable",
    "synthesis_timeout",
    "synthesis_failed",
    "playback_failed",
    "cancelled",
]


class SpeechOutcome(BaseModel):
    utterance_id: str = Field(min_length=1, max_length=64)
    state: UtteranceState
    error_code: Optional[VoiceSessionErrorCode] = None


class VoiceSessionSnapshot(BaseModel):
    """Safe state for a UI or device observer; it deliberately omits text/audio."""

    session_id: str = Field(min_length=1, max_length=64)
    state: VoiceSessionState
    queue_depth: int = Field(ge=0)
    current_utterance_id: Optional[str] = None
    sink_id: str = Field(min_length=1, max_length=32)
    last_error_code: Optional[VoiceSessionErrorCode] = None
