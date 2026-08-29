from enum import Enum
from typing import Optional

from yogurt_contracts.foundation.identifiers import ResponseId, TurnId
from yogurt_contracts.foundation.model import FrozenContract


class TurnAdmissionStatus(str, Enum):
    NEWLY_ADMITTED = "newly_admitted"
    SAME_LOGICAL_TURN = "same_logical_turn"
    TURN_ID_CONFLICT = "turn_id_conflict"
    CONVERSATION_BUSY = "conversation_busy"
    REJECTED = "rejected"


class AdmissionResponseState(str, Enum):
    NOT_STARTED = "not_started"
    STARTED = "started"
    FINALIZED = "finalized"
    ABORTED = "aborted"


class TurnAdmissionResult(FrozenContract):
    status: TurnAdmissionStatus
    turn_id: Optional[TurnId] = None
    response_id: Optional[ResponseId] = None
    response_state: AdmissionResponseState = AdmissionResponseState.NOT_STARTED
    reason: Optional[str] = None
