from enum import Enum

from pydantic import field_validator

from .model import FrozenContract


class ErrorCode(str, Enum):
    INVALID_ARGUMENT = "invalid_argument"; UNAUTHORIZED = "unauthorized"; FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"; TURN_ID_CONFLICT = "turn_id_conflict"; IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    STALE_GENERATION = "stale_generation"; INCOMPATIBLE_CONTRACT = "incompatible_contract"
    DEADLINE_EXCEEDED = "deadline_exceeded"; PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONTRACT_VIOLATION = "contract_violation"; RESOURCE_EXHAUSTED = "resource_exhausted"; INTERNAL = "internal"


class ErrorCategory(str, Enum):
    VALIDATION = "validation"; AUTHORIZATION = "authorization"; COMPATIBILITY = "compatibility"; CONFLICT = "conflict"
    AVAILABILITY = "availability"; TIMEOUT = "timeout"; RESOURCE = "resource"; INTERNAL = "internal"


class RetryDisposition(str, Enum):
    DO_NOT_RETRY = "do_not_retry"; RETRY_SAME_ID = "retry_same_id"
    RETRY_NEW_ATTEMPT_SAME_LOGICAL_OPERATION = "retry_new_attempt_same_logical_operation"
    RETRY_AFTER_REBIND = "retry_after_rebind"; MANUAL_REVIEW = "manual_review"


class OutcomeCertainty(str, Enum):
    KNOWN = "known"; UNKNOWN = "unknown"


class DomainError(FrozenContract):
    code: ErrorCode
    category: ErrorCategory
    retry: RetryDisposition
    certainty: OutcomeCertainty = OutcomeCertainty.KNOWN
    safe_message: str

    @field_validator("safe_message")
    @classmethod
    def safe_and_bounded(cls, value: str) -> str:
        if not value or len(value) > 512:
            raise ValueError("safe_message must be between 1 and 512 characters")
        return value
