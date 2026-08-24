from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

MouthEventKind = Literal[
    "queued",
    "synthesis_started",
    "audio_ready",
    "playback_started",
    "audio_chunk",
    "speech_finished",
    "speech_cancelled",
    "speech_failed",
]


class MouthEvent(BaseModel):
    """A bounded speech lifecycle event for future renderers and devices.

    P10-B emits lifecycle events only. `audio_chunk` is reserved for a future
    streaming runtime and is intentionally not emitted by the current
    full-audio synthesis path.
    """

    utterance_id: str = Field(min_length=1, max_length=64)
    kind: MouthEventKind
    timestamp_ms: int = Field(ge=0)
    # Shared by Mouth and VoiceSession. It is a bounded identifier, never an
    # upstream body, local path, command, or exception message.
    error_code: Optional[str] = Field(default=None, max_length=64, pattern=r"^[a-z_]+$")
