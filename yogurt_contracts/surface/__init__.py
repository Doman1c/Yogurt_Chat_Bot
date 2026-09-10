"""Provider-neutral Native Surface wire contracts. No authority-bearing input."""
from typing import Literal, Optional, Tuple
from pydantic import Field, field_validator
from yogurt_contracts.foundation.model import FrozenContract

SurfaceType = Literal["native_ios", "native_ipados", "native_macos"]

class SurfaceClientMessageId(FrozenContract):
    value: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")

class SurfaceConversationRef(FrozenContract):
    conversation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

class NativeSurfaceTurnRequest(FrozenContract):
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    client_message_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    text: str = Field(min_length=1, max_length=16000)
    client_timestamp: Optional[str] = Field(default=None, max_length=64)
    surface_type: SurfaceType
    device_instance_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("text")
    @classmethod
    def visible(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty text")
        return value

class SurfaceTurnAccepted(FrozenContract):
    conversation_id: str
    turn_ref: str
    response_id: str
    event_stream_url: str
    admission: Literal["NEWLY_ADMITTED", "SAME_LOGICAL_TURN"]

class SurfaceResponseEvent(FrozenContract):
    response_id: str
    sequence: int = Field(ge=1)
    event: Literal["response.started", "response.delta", "response.text", "response.completed", "response.failed", "response.cancelled"]
    text: Optional[str] = None
    reason: Optional[str] = None

class SurfaceError(FrozenContract):
    code: str

class SurfaceHistoryTurn(FrozenContract):
    turn_ref: str
    text: str
    response_id: Optional[str]
    response_text: str
    state: str

class SurfaceConversationHistory(SurfaceConversationRef):
    turns: Tuple[SurfaceHistoryTurn, ...]
