from enum import Enum


class TurnState(str, Enum):
    RECEIVED = "received"; INPUT_COMMITTED = "input_committed"; PROCESSING = "processing"
    COMPLETED = "completed"; REJECTED = "rejected"; CANCELLED = "cancelled"; ABORTED = "aborted"


class AbortReason(str, Enum):
    RECOVERY = "recovery"; EXECUTION_FAILURE = "execution_failure"; PROVIDER_FAILURE = "provider_failure"; POLICY = "policy"


_TRANSITIONS = {
    TurnState.RECEIVED: {TurnState.INPUT_COMMITTED, TurnState.REJECTED},
    TurnState.INPUT_COMMITTED: {TurnState.PROCESSING, TurnState.CANCELLED, TurnState.ABORTED},
    TurnState.PROCESSING: {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED},
}


def validate_turn_transition(current: TurnState, target: TurnState) -> bool:
    return target in _TRANSITIONS.get(current, set())
