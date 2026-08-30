"""Durable Core boundary for atomic R3-B operation-to-sequence ownership."""

from typing import Protocol

from yogurt_contracts.memory.causal import (
    MemoryCausalLaneSnapshot,
    MemoryCausalOperation,
    MemoryCausalResolution,
    MemoryObservationOperation,
)
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_core.ports.state_store import StateCommit
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken


class MemoryCausalStateStore(Protocol):
    """Implementations commit the semantic observation intent and sequence association atomically."""

    async def commit_memory_observation_intent(
        self,
        token: CoreAuthorityToken,
        operation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
    ) -> StateCommit[MemoryCausalOperation]:
        ...

    async def get_memory_causal_lane(self, operation: MemoryObservationOperation) -> MemoryCausalLaneSnapshot:
        ...

    async def project_memory_operation_outcome(
        self, token: CoreAuthorityToken, operation: MemoryObservationOperation
    ) -> StateCommit[MemoryCausalLaneSnapshot]:
        ...

    async def reconcile_memory_observation(
        self, token: CoreAuthorityToken, operation: MemoryObservationOperation, resolution: MemoryCausalResolution
    ) -> StateCommit[MemoryCausalLaneSnapshot]:
        ...
