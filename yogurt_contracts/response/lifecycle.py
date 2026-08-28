from enum import Enum


class ResponseState(str, Enum):
    STARTED = "started"; FINALIZED = "finalized"; ABORTED = "aborted"


def validate_response_transition(current: ResponseState, target: ResponseState) -> bool:
    return current == ResponseState.STARTED and target in {ResponseState.FINALIZED, ResponseState.ABORTED}
