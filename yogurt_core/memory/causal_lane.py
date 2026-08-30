"""Core-owned R3-B causal lane; provider runtime state is never authoritative."""

from typing import Dict, Optional

from yogurt_contracts.foundation.identifiers import OperationId
from yogurt_contracts.memory.causal import (
    MemoryCausalLaneSnapshot,
    MemoryCausalOperation,
    MemoryCausalResolution,
    MemoryCausalState,
    MemoryObservationOperation,
    MemoryObservationParticipation,
    MemoryObservation,
    memory_observation_participation,
)
from yogurt_contracts.memory.recall import RecallEligibility

from yogurt_core.operations.models import DurableOperationState, StoredOperation


class MemoryCausalLaneError(RuntimeError):
    pass


class MemoryCausalLane:
    """Deterministic sequence allocator and resolution projection for one logical domain."""

    def __init__(self, snapshot: MemoryCausalLaneSnapshot) -> None:
        self._state = snapshot.state
        self._operations = {operation.operation_id: operation for operation in snapshot.operations}  # type: Dict[OperationId, MemoryCausalOperation]

    @classmethod
    def empty(cls, operation: MemoryObservationOperation) -> "MemoryCausalLane":
        state = MemoryCausalState(
            scope_ref=operation.scope_ref,
            logical_provider_domain=operation.logical_provider_domain,
        )
        return cls(MemoryCausalLaneSnapshot(state=state))

    @property
    def state(self) -> MemoryCausalState:
        return self._state

    def snapshot(self) -> MemoryCausalLaneSnapshot:
        return MemoryCausalLaneSnapshot(
            state=self._state,
            operations=tuple(sorted(self._operations.values(), key=lambda item: item.sequence)),
        )

    def recall_eligibility(self) -> RecallEligibility:
        if self._state.first_unresolved_sequence is not None or not self._state.history_complete:
            return RecallEligibility.HISTORY_INCOMPLETE
        return RecallEligibility.ELIGIBLE

    def allocate(self, operation: MemoryObservationOperation) -> MemoryCausalOperation:
        self._require_domain_and_scope(operation)
        present = self._operations.get(operation.operation_id)
        if present is not None:
            if present.observation_fingerprint != operation.observation_fingerprint:
                raise MemoryCausalLaneError("MEMORY_OPERATION_ID_CONFLICT")
            return present
        allocated = MemoryCausalOperation(
            operation_id=operation.operation_id,
            sequence=self._state.next_sequence,
            observation_fingerprint=operation.observation_fingerprint,
        )
        self._operations[allocated.operation_id] = allocated
        self._state = self._state.model_copy(
            update={"next_sequence": self._state.next_sequence + 1, "revision": self._state.revision + 1}
        )
        return allocated

    def can_dispatch(self, operation_id: OperationId) -> bool:
        operation = self._operations.get(operation_id)
        return (
            operation is not None
            and operation.resolution == MemoryCausalResolution.PENDING
            and self._state.first_unresolved_sequence is None
            and operation.sequence == self._state.resolved_through_sequence + 1
        )

    def record_durable_operation(self, operation: StoredOperation) -> Optional[MemoryCausalOperation]:
        """Project existing R2-D operation states into the causal authority state."""

        causal = self._operations.get(operation.intent.operation.operation_id)
        if causal is None:
            return None
        mapping = {
            DurableOperationState.SUCCEEDED: MemoryCausalResolution.APPLIED,
            DurableOperationState.FAILED: MemoryCausalResolution.RESOLVED_NOT_APPLIED,
            DurableOperationState.NOT_DISPATCHED: MemoryCausalResolution.RESOLVED_NOT_APPLIED,
            DurableOperationState.OUTCOME_UNKNOWN: MemoryCausalResolution.UNRESOLVED,
        }
        resolution = mapping.get(operation.state)
        if resolution is None:
            return causal
        return self.resolve(causal.operation_id, resolution)

    def resolve(self, operation_id: OperationId, resolution: MemoryCausalResolution) -> MemoryCausalOperation:
        if resolution == MemoryCausalResolution.PENDING:
            raise MemoryCausalLaneError("MEMORY_CAUSAL_RESOLUTION_REQUIRED")
        present = self._operations.get(operation_id)
        if present is None:
            raise MemoryCausalLaneError("MEMORY_OPERATION_NOT_FOUND")
        if present.resolution not in {MemoryCausalResolution.PENDING, MemoryCausalResolution.UNRESOLVED}:
            if present.resolution != resolution:
                raise MemoryCausalLaneError("MEMORY_CAUSAL_RESOLUTION_CONFLICT")
            return present
        updated = present.model_copy(update={"resolution": resolution})
        self._operations[updated.operation_id] = updated
        self._reproject()
        return updated

    def reconcile_same_domain(self, operation: MemoryObservationOperation, resolution: MemoryCausalResolution) -> MemoryCausalOperation:
        """A restart may resolve uncertainty only with proof from the same logical domain."""

        self._require_domain_and_scope(operation)
        return self.resolve(operation.operation_id, resolution)

    def _require_domain_and_scope(self, operation: MemoryObservationOperation) -> None:
        if operation.scope_ref != self._state.scope_ref:
            raise MemoryCausalLaneError("MEMORY_CAUSAL_SCOPE_MISMATCH")
        if operation.logical_provider_domain != self._state.logical_provider_domain:
            raise MemoryCausalLaneError("CROSS_DOMAIN_UNCERTAINTY_REJECTED")

    def _reproject(self) -> None:
        ordered = {operation.sequence: operation for operation in self._operations.values()}
        resolved = 0
        applied = 0
        while True:
            operation = ordered.get(resolved + 1)
            if operation is None or operation.resolution not in {
                MemoryCausalResolution.APPLIED, MemoryCausalResolution.RESOLVED_NOT_APPLIED,
            }:
                break
            resolved += 1
            if operation.resolution == MemoryCausalResolution.APPLIED and applied == resolved - 1:
                applied = resolved
        unresolved = tuple(
            operation.sequence for operation in ordered.values()
            if operation.resolution == MemoryCausalResolution.UNRESOLVED
        )
        ever_not_applied = not self._state.history_complete or any(
            operation.resolution == MemoryCausalResolution.RESOLVED_NOT_APPLIED
            for operation in ordered.values()
        )
        self._state = self._state.model_copy(update={
            "resolved_through_sequence": resolved,
            "applied_through_sequence": applied,
            "first_unresolved_sequence": min(unresolved) if unresolved else None,
            "history_complete": not ever_not_applied,
            "revision": self._state.revision + 1,
        })


class MemoryObservationSequencer:
    """Prevents non-participating observations from consuming a causal sequence."""

    def __init__(self, lane: MemoryCausalLane) -> None:
        self._lane = lane

    def register(
        self,
        observation: MemoryObservation,
        semantic_operation: Optional[MemoryObservationOperation] = None,
    ) -> Optional[MemoryCausalOperation]:
        if memory_observation_participation(observation) == MemoryObservationParticipation.DOES_NOT_PARTICIPATE:
            return None
        if semantic_operation is None or semantic_operation.observation != observation:
            raise MemoryCausalLaneError("MEMORY_SEMANTIC_OPERATION_MISMATCH")
        return self._lane.allocate(semantic_operation)
