"""R3-B bridge from semantic observations to the existing R2-D operation engine."""

from dataclasses import dataclass

from yogurt_contracts.foundation.identifiers import CapabilityId
from yogurt_contracts.memory.causal import MemoryCausalOperation, MemoryDispatchAttempt, MemoryObservationOperation
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken

from yogurt_core.memory.causal_lane import MemoryCausalLane
from yogurt_core.operations.engine import OperationEngine
from yogurt_core.operations.models import ProviderDispatchResult, StoredOperation
from yogurt_core.operations.provider import OperationProviderPort
from yogurt_core.ports.memory_observation import MemoryCausalStateStore
from yogurt_core.ports.state_store import CoreStateError


@dataclass(frozen=True)
class MemoryObservationExecution:
    causal_operation: MemoryCausalOperation
    durable_operation: StoredOperation


class _AttemptBoundMemoryProvider:
    """Ensures the engine can dispatch only the binding committed by the Memory bridge."""

    def __init__(self, provider: OperationProviderPort, attempt: MemoryDispatchAttempt) -> None:
        self._provider = provider
        self._attempt = attempt

    @property
    def capability_id(self) -> CapabilityId:
        return self._provider.capability_id

    async def dispatch(self, intent: OperationIntentCommitted) -> ProviderDispatchResult:
        if (
            intent.operation.operation_id != self._attempt.dispatch_attempt.operation_id
            or intent.provider_binding != self._attempt.provider_binding
        ):
            raise CoreStateError("MEMORY_DISPATCH_ATTEMPT_BINDING_MISMATCH")
        return await self._provider.dispatch(intent)


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
        if provider.capability_id != intent.operation.target_capability:
            durable_operation = await self._operation_engine.execute(token, intent, provider)
        else:
            attempt = await self._causal_store.commit_memory_dispatch_attempt(token, observation, intent)
            if attempt.value is None or attempt.certainty.value != "committed":
                raise CoreStateError("MEMORY_DISPATCH_ATTEMPT_NOT_COMMITTED")
            durable_operation = await self._operation_engine.execute(
                token,
                intent,
                _AttemptBoundMemoryProvider(provider, attempt.value),
            )
        projected = await self._causal_store.project_memory_operation_outcome(token, observation)
        if projected.value is None:
            raise CoreStateError("MEMORY_CAUSAL_STATE_NOT_COMMITTED")
        return MemoryObservationExecution(causal_operation=causal_operation, durable_operation=durable_operation)
