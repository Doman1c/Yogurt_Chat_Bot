from __future__ import annotations

from pydantic import BaseModel, Field


class RecallRequest(BaseModel):
    persona_id: str = "yogurt"
    user_id: str
    surface: str
    conversation_key: str
    message: str = Field(max_length=8000)
    max_tokens: int | None = Field(default=None, ge=0, le=350)
    max_items: int | None = Field(default=None, ge=1, le=3)
    turn_id: str | None = Field(default=None, max_length=180)


class ObserveRequest(BaseModel):
    persona_id: str = "yogurt"
    user_id: str
    surface: str
    conversation_key: str
    phase: str
    user_message: str = Field(default="", max_length=8000)
    assistant_reply: str = Field(default="", max_length=16000)
    idempotency_key: str | None = Field(default=None, max_length=180)
