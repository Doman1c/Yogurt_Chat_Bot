"""R3-B bridge from semantic observations to the existing R2-D operation engine."""

from dataclasses import dataclass

from yogurt_contracts.memory.causal import MemoryCausalOperation, MemoryObservationOperation
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken

from yogurt_core.memory.causal_lane import MemoryCausalLane
from yogurt_core.operations.engine import OperationEngine
from yogurt_core.operations.models import StoredOperation
from yogurt_core.operations.provider import OperationProviderPort
from yogurt_core.ports.memory_observation import MemoryCausalStateStore
from yogurt_core.ports.state_store import CoreStateError


@dataclass(frozen=True)
class MemoryObservationExecution:
    causal_operation: MemoryCausalOperation
    durable_operation: StoredOperation


class MemoryObservationOperationCoordinator:
    """Uses R2-D's write-ahead operation engine; a blocked lane never blocks a Core turn."""

    def __init__(self, causal_store: MemoryCausalStateStore, operation_engine: OperationEngine) -> None:
        self._causal_store = causal_store
        self._operation_engine = operation_engine

    async def execute(
        self,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
        provider: OperationProviderPort,
    ) -> MemoryObservationExecution:
        observation.validate_durable_intent(intent)
        committed = await self._causal_store.commit_memory_observation_intent(token, observation, intent)
        if committed.value is None:
            raise CoreStateError("MEMORY_CAUSAL_STATE_NOT_COMMITTED")
        causal_operation = committed.value
        lane = MemoryCausalLane(await self._causal_store.get_memory_causal_lane(observation))
        if not lane.can_dispatch(causal_operation.operation_id):
            # The durable intent is already committed.  A causal predecessor
            # being unresolved is not proof of no effect for this operation.
            return MemoryObservationExecution(
                causal_operation=causal_operation,
                durable_operation=await self._causal_store.get_operation(causal_operation.operation_id),
            )
        durable_operation = await self._operation_engine.execute(token, intent, provider)
        projected = await self._causal_store.project_memory_operation_outcome(token, observation)
        if projected.value is None:
            raise CoreStateError("MEMORY_CAUSAL_STATE_NOT_COMMITTED")
        return MemoryObservationExecution(causal_operation=causal_operation, durable_operation=durable_operation)
