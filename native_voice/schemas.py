"""Public, privacy-bounded values for Native Voice turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


SUPPORTED_LANGUAGES = ("auto", "zh", "ja", "en")


class TurnPhase(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TurnStatus(str, Enum):
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"


def normalize_language(value: str) -> str:
    language = (value or "").strip().lower()
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError("unsupported_language")
    return language


@dataclass(frozen=True)
class TranscriptResult:
    """In-memory STT output. It is never included in NativeVoiceEvent."""

    text: str
    language: str = "auto"
    duration_ms: Optional[int] = None

    def __post_init__(self) -> None:
        text = " ".join(self.text.split())
        if not text:
            raise ValueError("empty_transcript")
        if len(text) > 4000:
            raise ValueError("transcript_too_long")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "language", normalize_language(self.language))


@dataclass(frozen=True)
class BrainReply:
    """Final assistant text only; no prompt, tool trace, or reasoning."""

    text: str
    model_id: Optional[str] = None

    def __post_init__(self) -> None:
        text = self.text.strip()
        if not text:
            raise ValueError("empty_brain_reply")
        if len(text) > 500:
            raise ValueError("brain_reply_too_long")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True)
class NativeVoiceEvent:
    """Safe lifecycle telemetry: intentionally contains neither audio nor text."""

    session_id: str
    turn_id: str
    phase: TurnPhase
    status: TurnStatus
    duration_ms: Optional[int] = None
    error_code: Optional[str] = None

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "phase": self.phase.value,
            "status": self.status.value,
            "duration_ms": str(self.duration_ms) if self.duration_ms is not None else None,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class NativeVoiceTurn:
    """An in-memory result for one opaque Native Voice turn."""

    session_id: str
    turn_id: str
    phase: TurnPhase
    status: TurnStatus
    transcript: Optional[TranscriptResult] = None
    reply: Optional[BrainReply] = None
    error_code: Optional[str] = None
    latencies_ms: Dict[str, int] = field(default_factory=dict)


def native_conversation_key(session_id: str) -> str:
    """Return the isolated OpenClaw scope used by Native Voice only."""

    value = (session_id or "").strip()
    if not value or len(value) > 64:
        raise ValueError("invalid_session_id")
    return "native:voice:" + value
