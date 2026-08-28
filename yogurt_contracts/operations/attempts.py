from typing import Optional

from ..foundation.identifiers import BindingId, OperationAttemptId, OperationId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant


class DispatchAttempt(FrozenContract):
    operation_attempt_id: OperationAttemptId
    operation_id: OperationId
    attempt_number: int
    provider_binding_id: BindingId
    dispatch_started_at: UtcInstant
    dispatch_finished_at: Optional[UtcInstant] = None
    provider_reference: Optional[str] = None
    evidence: Optional[str] = None
