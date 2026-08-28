from enum import Enum


class OperationOutcome(str, Enum):
    SUCCEEDED = "succeeded"; FAILED = "failed"; OUTCOME_UNKNOWN = "outcome_unknown"
