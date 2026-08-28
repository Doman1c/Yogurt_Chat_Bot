from enum import Enum


class CancellationDisposition(str, Enum):
    NOT_REQUESTED = "not_requested"; CANCEL_REQUESTED = "cancel_requested"
    CANCELLED_BEFORE_DISPATCH = "cancelled_before_dispatch"; CANCEL_ACKNOWLEDGED = "cancel_acknowledged"
    TOO_LATE = "too_late"; NOT_SUPPORTED = "not_supported"; OUTCOME_UNKNOWN = "outcome_unknown"
