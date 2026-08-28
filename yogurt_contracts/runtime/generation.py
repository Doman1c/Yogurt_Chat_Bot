from typing import Optional

from ..foundation.identifiers import GenerationAuthorityId, LeaseId, RuntimeInstanceId, RuntimeSlotId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant


class GenerationAssignment(FrozenContract):
    runtime_slot_id: RuntimeSlotId
    generation: int
    runtime_instance_id: RuntimeInstanceId
    issued_by: GenerationAuthorityId
    issued_at: UtcInstant
    authority_revision: str
    lease_id: Optional[LeaseId] = None
