from enum import Enum
from typing import Optional

from ..conversation.refs import TurnRef
from ..foundation.identifiers import CapabilityId, OperationId
from ..foundation.idempotency import RequestFingerprint
from ..foundation.model import FrozenContract
from ..foundation.time import Deadline, UtcInstant
from ..identity.authority import DelegatedAuthority
from ..capability.binding import CapabilityBinding
from .traits import OperationTraits


class LogicalOperation(FrozenContract):
    operation_id: OperationId
    request_fingerprint: RequestFingerprint
    target_capability: CapabilityId
    authority: DelegatedAuthority
    deadline: Deadline
    traits: OperationTraits
    created_at: UtcInstant
    turn_ref: Optional[TurnRef] = None


class OperationLifecycleState(str, Enum):
    INTENT_COMMITTED = "intent_committed"
    DISPATCH_ATTEMPTED = "dispatch_attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class OperationIntentCommitted(FrozenContract):
    """Write-ahead intent fact required before externally side-effecting dispatch."""

    operation: LogicalOperation
    provider_binding: CapabilityBinding
    required_durability: str
    committed_at: UtcInstant
    state: OperationLifecycleState = OperationLifecycleState.INTENT_COMMITTED
