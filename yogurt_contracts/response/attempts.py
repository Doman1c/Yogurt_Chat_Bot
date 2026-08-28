from enum import Enum
from typing import Optional

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
