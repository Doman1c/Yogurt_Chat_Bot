from typing import Optional, Protocol

from pydantic import field_validator

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

    @field_validator("generation")
    @classmethod
    def has_positive_generation(cls, value: int) -> int:
        if value < 1:
            raise ValueError("generation must be at least 1")
        return value

    @field_validator("authority_revision")
    @classmethod
    def has_authority_revision(cls, value: str) -> str:
        if not value:
            raise ValueError("authority_revision must be non-empty")
        return value


class GenerationAuthority(Protocol):
    """Durable per-slot monotonic issuer; a runtime self-claim is never authority evidence."""

    def issue(self, runtime_slot_id: RuntimeSlotId, runtime_instance_id: RuntimeInstanceId) -> GenerationAssignment:
        ...
