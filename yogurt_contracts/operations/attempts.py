from typing import Optional

from pydantic import model_validator

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

    @model_validator(mode="after")
    def has_valid_attempt_times(self) -> "DispatchAttempt":
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be at least 1")
        if self.dispatch_finished_at is not None and self.dispatch_finished_at.value < self.dispatch_started_at.value:
            raise ValueError("dispatch attempt finished_at must not precede started_at")
        return self
