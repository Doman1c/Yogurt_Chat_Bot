"""Provider-neutral durable operation state used by the R2-D engine."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from yogurt_contracts.operations.attempts import DispatchAttempt
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_contracts.operations.outcome import OperationOutcome


class DurableOperationState(str, Enum):
    INTENT_COMMITTED = "intent_committed"
    NOT_DISPATCHED = "not_dispatched"
    DISPATCH_ATTEMPTED = "dispatch_attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class NotDispatchedReason(str, Enum):
    AUTHORITY_REVOKED = "authority_revoked"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PROVIDER_NOT_AVAILABLE = "provider_not_available"
    CANCELLED_BEFORE_DISPATCH = "cancelled_before_dispatch"
    CORE_NOT_SERVING = "core_not_serving"
    POLICY_REJECTED = "policy_rejected"


class ProviderDispatchCertainty(str, Enum):
    SUCCESS = "success"
    DEFINITE_FAILURE = "definite_failure"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class ProviderDispatchResult:
    certainty: ProviderDispatchCertainty
    provider_reference: Optional[str] = None
    evidence: Optional[str] = None


@dataclass(frozen=True)
class StoredOperation:
    intent: OperationIntentCommitted
    state: DurableOperationState
    revision: int
    attempts: Tuple[DispatchAttempt, ...] = ()
    outcome: Optional[OperationOutcome] = None
    not_dispatched_reason: Optional[NotDispatchedReason] = None
