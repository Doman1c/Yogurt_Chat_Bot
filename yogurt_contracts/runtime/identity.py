from pydantic import field_validator

from ..foundation.identifiers import NodeId, RuntimeInstanceId, RuntimeSlotId
from ..foundation.model import FrozenContract


class RuntimeIdentity(FrozenContract):
    runtime_type: str
    runtime_slot_id: RuntimeSlotId
    runtime_instance_id: RuntimeInstanceId
    runtime_generation: int
    node_id: NodeId

    @field_validator("runtime_generation")
    @classmethod
    def has_positive_generation(cls, value: int) -> int:
        if value < 1:
            raise ValueError("runtime_generation must be at least 1")
        return value
from pydantic import field_validator
