"""Durable Core boundary for atomic R3-B operation-to-sequence ownership."""

from typing import Protocol

from yogurt_contracts.memory.causal import (
    MemoryCausalLaneSnapshot,
    MemoryCausalOperation,
    MemoryCausalResolution,
    MemoryDispatchAttempt,
    MemoryObservationOperation,
    MemoryReconciliationProof,
)
from yogurt_contracts.foundation.identifiers import OperationId
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_core.ports.state_store import StateCommit
from yogurt_core.operations.models import StoredOperation
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

    async def get_operation(self, operation_id: OperationId) -> StoredOperation:
        ...

    async def commit_memory_dispatch_attempt(
        self,
        token: CoreAuthorityToken,
        operation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
    ) -> StateCommit[MemoryDispatchAttempt]:
        ...

    async def get_memory_dispatch_attempts(
        self, operation_id: OperationId,
    ) -> tuple[MemoryDispatchAttempt, ...]:
        ...

    async def project_memory_operation_outcome(
        self, token: CoreAuthorityToken, operation: MemoryObservationOperation
    ) -> StateCommit[MemoryCausalLaneSnapshot]:
        ...

    async def reconcile_memory_observation(
        self,
        token: CoreAuthorityToken,
        operation: MemoryObservationOperation,
        resolution: MemoryCausalResolution,
        proof: MemoryReconciliationProof,
    ) -> StateCommit[MemoryCausalLaneSnapshot]:
        ...
