from typing import Optional

from ..conversation.refs import TurnRef
from ..foundation.identifiers import CapabilityId, OperationId
from ..foundation.idempotency import RequestFingerprint
from ..foundation.model import FrozenContract
from ..foundation.time import Deadline, UtcInstant
from ..identity.authority import DelegatedAuthority
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
