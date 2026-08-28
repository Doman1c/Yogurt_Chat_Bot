from enum import Enum
from typing import Optional

from pydantic import model_validator

from ..foundation.identifiers import BindingId, ExecutionAttemptId, ResponseId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant


class AttemptDisposition(str, Enum):
    STARTED = "started"; FAILED = "failed"; SUCCEEDED = "succeeded"; ABANDONED = "abandoned"


class ExecutionAttempt(FrozenContract):
    execution_attempt_id: ExecutionAttemptId
    response_id: ResponseId
    attempt_number: int
    provider_binding_id: BindingId
    started_at: UtcInstant
    ended_at: Optional[UtcInstant] = None
    disposition: AttemptDisposition = AttemptDisposition.STARTED
    provider_reference: Optional[str] = None

    @model_validator(mode="after")
    def has_valid_lifecycle_times(self) -> "ExecutionAttempt":
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be at least 1")
        terminal = self.disposition != AttemptDisposition.STARTED
        if terminal and self.ended_at is None:
            raise ValueError("terminal execution attempt requires ended_at")
        if self.ended_at is not None and self.ended_at.value < self.started_at.value:
            raise ValueError("execution attempt ended_at must not precede started_at")
        return self
