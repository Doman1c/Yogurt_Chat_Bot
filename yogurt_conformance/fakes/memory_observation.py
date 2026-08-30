"""R3-B deterministic ordered provider fake; it owns no authoritative causal state."""

from typing import Tuple

from yogurt_contracts.memory.causal import MemoryCausalOperation, MemoryCausalResolution
from yogurt_core.memory.causal_lane import MemoryCausalLane


class FakeOrderedMemoryProvider:
    def __init__(self, logical_provider_domain: str) -> None:
        self.logical_provider_domain = logical_provider_domain
        self.applied_sequences = ()  # type: Tuple[int, ...]

    def apply(self, lane: MemoryCausalLane, operation: MemoryCausalOperation) -> bool:
        if self.logical_provider_domain != lane.state.logical_provider_domain:
            return False
        if not lane.can_dispatch(operation.operation_id):
            return False
        lane.resolve(operation.operation_id, MemoryCausalResolution.APPLIED)
        self.applied_sequences += (operation.sequence,)
        return True

    def reconcile(self, lane: MemoryCausalLane, operation: MemoryCausalOperation) -> None:
        if self.logical_provider_domain != lane.state.logical_provider_domain:
            raise ValueError("CROSS_DOMAIN_UNCERTAINTY_REJECTED")
        lane.resolve(operation.operation_id, MemoryCausalResolution.APPLIED)
