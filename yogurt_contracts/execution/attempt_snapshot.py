from typing import Optional, Tuple

from ..capability.binding import CapabilityBinding
from ..foundation.identifiers import ExecutionAttemptId, ResponseId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant


class ExecutionAttemptSnapshot(FrozenContract):
    execution_attempt_id: ExecutionAttemptId
    response_id: ResponseId
    binding_snapshots: Tuple[CapabilityBinding, ...]
    started_at: UtcInstant
    provider_applied_config_revision: Optional[str] = None
