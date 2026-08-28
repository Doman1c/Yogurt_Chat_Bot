from enum import Enum
from typing import Optional

from ..conversation.refs import TurnRef
from pydantic import model_validator

from ..execution.authorization_snapshot import OperationAuthorizationSnapshot
from ..foundation.identifiers import CapabilityId, OperationId
from ..foundation.idempotency import RequestFingerprint
from ..foundation.durability import DurabilityLevel
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

    @model_validator(mode="after")
    def is_created_before_deadline(self) -> "LogicalOperation":
        if self.created_at.value > self.deadline.expires_at.value:
            raise ValueError("operation created_at must not follow its deadline")
        return self


class OperationLifecycleState(str, Enum):
    INTENT_COMMITTED = "intent_committed"
    DISPATCH_ATTEMPTED = "dispatch_attempted"
    TERMINAL = "terminal"


class OperationIntentCommitted(FrozenContract):
    """Write-ahead intent fact required before externally side-effecting dispatch."""

    operation: LogicalOperation
    provider_binding: CapabilityBinding
    authorization_snapshot: OperationAuthorizationSnapshot
    required_durability: DurabilityLevel
    committed_at: UtcInstant
    state: OperationLifecycleState = OperationLifecycleState.INTENT_COMMITTED

    @model_validator(mode="after")
    def has_correlated_authorization(self) -> "OperationIntentCommitted":
        if self.authorization_snapshot.operation_id != self.operation.operation_id:
            raise ValueError("operation intent authorization snapshot must match operation")
        return self
