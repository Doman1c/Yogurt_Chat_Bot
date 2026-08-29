from enum import Enum


class ResponseState(str, Enum):
    STARTED = "started"; FINALIZED = "finalized"; ABORTED = "aborted"


class ResponseAbortReason(str, Enum):
    """Authoritative semantic cause for an aborted logical response."""

    CANCELLED = "cancelled"
    PROVIDER_FAILURE = "provider_failure"
    EXECUTION_FAILURE = "execution_failure"
    POLICY = "policy"
    RECOVERY = "recovery"


def validate_response_transition(current: ResponseState, target: ResponseState) -> bool:
    return current == ResponseState.STARTED and target in {ResponseState.FINALIZED, ResponseState.ABORTED}
