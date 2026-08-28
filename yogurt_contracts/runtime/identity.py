from ..foundation.identifiers import NodeId, RuntimeInstanceId, RuntimeSlotId
from ..foundation.model import FrozenContract


class RuntimeIdentity(FrozenContract):
    runtime_type: str
    runtime_slot_id: RuntimeSlotId
    runtime_instance_id: RuntimeInstanceId
    runtime_generation: int
    node_id: NodeId
