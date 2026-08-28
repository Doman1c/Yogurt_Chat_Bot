"""Turn identity is key-plus-fingerprint, never a trace correlation ID."""

from enum import Enum
from ..foundation.durability import DurabilityLevel
from ..foundation.identifiers import TurnId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from .fingerprint import TurnFingerprint
from .refs import ConversationRef


class TurnIdempotencyKey(FrozenContract):
    """Canonical logical turn identity; TurnRef metadata is intentionally excluded."""

    conversation_ref: ConversationRef
    turn_id: TurnId


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
