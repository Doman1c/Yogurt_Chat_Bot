"""Turn identity is key-plus-fingerprint, never a trace correlation ID."""

from enum import Enum
from typing import Any

from ..foundation.durability import DurabilityLevel
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from .fingerprint import TurnFingerprint
from .refs import ConversationRef, TurnRef


class TurnIdempotencyKey(FrozenContract):
    conversation_ref: ConversationRef
    turn_ref: TurnRef

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if self.conversation_ref.conversation_id != self.turn_ref.conversation_id:
            raise ValueError("TurnIdempotencyKey conversation and TurnRef must match")


class TurnRetryDecision(str, Enum):
    SAME_LOGICAL_TURN = "same_logical_turn"
    TURN_ID_CONFLICT = "turn_id_conflict"
    DIFFERENT_TURN = "different_turn"


class TurnIdempotencyRecord(FrozenContract):
    key: TurnIdempotencyKey
    fingerprint: TurnFingerprint
    admission_result: str
    committed_at: UtcInstant
    required_durability: DurabilityLevel
