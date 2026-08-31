"""R3-C causal preconditions around the provider-neutral recall dispatcher."""

from __future__ import annotations

import time
from typing import Optional

from yogurt_contracts.execution import CapabilityAuthorizationSnapshot
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.causal import (
    MemoryCausalLaneSnapshot,
    MemoryCausalResolution,
    MemoryObservationOperation,
)
from yogurt_contracts.memory.recall import (
    MemoryRecallBindingSnapshot,
    MemoryRecallDispatchResult,
    MemoryRecallQuery,
    RecallEligibility,
)
from yogurt_core.memory.observation_operations import MemoryObservationExecution, MemoryObservationOperationCoordinator
from yogurt_core.memory.recall_policy import MemoryRecallDispatcher
from yogurt_core.memory.recall_policy import MemoryContextAdmission, MemoryContextRejectReason
from yogurt_core.operations.provider import OperationProviderPort
from yogurt_core.ports.memory_observation import MemoryCausalStateStore
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken

from .shadow import ShadowAuditStream


def memory_v5_recall_precondition(
    snapshot: MemoryCausalLaneSnapshot,
    current_observation: MemoryObservationOperation,
) -> RecallEligibility:
    """Never let legacy V5 recall create the current observation implicitly."""

    if snapshot.state.scope_ref != current_observation.scope_ref:
        return RecallEligibility.INVALID_SCOPE
    if not snapshot.state.history_complete:
        return RecallEligibility.HISTORY_INCOMPLETE
    current = next(
        (item for item in snapshot.operations if item.operation_id == current_observation.operation_id),
        None,
    )
    if current is None:
        return RecallEligibility.OBSERVATION_NOT_CONFIRMED
    if current.resolution in {MemoryCausalResolution.PENDING, MemoryCausalResolution.UNRESOLVED}:
        return RecallEligibility.PRIOR_CAUSAL_OBSERVATION_PENDING
    if current.resolution != MemoryCausalResolution.APPLIED:
        return RecallEligibility.OBSERVATION_NOT_CONFIRMED
    if snapshot.state.first_unresolved_sequence is not None:
        return RecallEligibility.PRIOR_CAUSAL_OBSERVATION_PENDING
    return RecallEligibility.ELIGIBLE


class MemoryV5ShadowRecallFlow:
    """Calls the existing Core dispatcher only after the causal V5 precondition."""

    def __init__(self, dispatcher: MemoryRecallDispatcher, audit: ShadowAuditStream) -> None:
        self._dispatcher = dispatcher
        self._audit = audit

    async def recall(
        self,
        query: MemoryRecallQuery,
        binding: MemoryRecallBindingSnapshot,
        authorization: CapabilityAuthorizationSnapshot,
        now: UtcInstant,
        snapshot: MemoryCausalLaneSnapshot,
        current_observation: MemoryObservationOperation,
    ) -> MemoryRecallDispatchResult:
        started = time.monotonic()
        eligibility = memory_v5_recall_precondition(snapshot, current_observation)
        result = await self._dispatcher.recall(query, binding, authorization, now, eligibility)
        evidence = result.evidence
        self._audit.record_recall(
            scope_reference=query.memory_conversation_scope_ref.scope_id.value,
            logical_provider_domain=binding.logical_provider_id,
            binding_id=binding.binding_id.value,
            runtime_generation=binding.runtime_generation,
            recall_eligibility=result.eligibility.value,
            provider_call_outcome=None if result.call_outcome is None else result.call_outcome.value,
            semantic_recall_decision=None if evidence is None else evidence.decision.value,
            candidate_count=0 if evidence is None else len(evidence.candidates),
            self_recall_filtered_count=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result

    def record_admission(
        self,
        query: MemoryRecallQuery,
        binding: MemoryRecallBindingSnapshot,
        result: MemoryRecallDispatchResult,
        admission: MemoryContextAdmission,
    ) -> None:
        """Add post-filter audit without exporting candidate text or identity."""

        filtered = int(
            MemoryContextRejectReason.CURRENT_TURN_SELF_RECALL in admission.filtered_reasons
            or admission.reason == MemoryContextRejectReason.SELF_RECALL_FILTERED_EMPTY
        )
        self._audit.record_recall(
            scope_reference=query.memory_conversation_scope_ref.scope_id.value,
            logical_provider_domain=binding.logical_provider_id,
            binding_id=binding.binding_id.value,
            runtime_generation=binding.runtime_generation,
            recall_eligibility=result.eligibility.value,
            provider_call_outcome=None if result.call_outcome is None else result.call_outcome.value,
            semantic_recall_decision=None if result.evidence is None else result.evidence.decision.value,
            candidate_count=0 if result.evidence is None else len(result.evidence.candidates),
            self_recall_filtered_count=filtered,
            latency_ms=0,
        )


class MemoryV5ShadowObservationFlow:
    """Records complete causal observation outcomes after the Core bridge returns."""

    def __init__(
        self,
        coordinator: MemoryObservationOperationCoordinator,
        causal_store: MemoryCausalStateStore,
        audit: ShadowAuditStream,
    ) -> None:
        self._coordinator = coordinator
        self._causal_store = causal_store
        self._audit = audit

    async def execute(
        self,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
        provider: OperationProviderPort,
    ) -> MemoryObservationExecution:
        started = time.monotonic()
        result = await self._coordinator.execute(token, observation, intent, provider)
        lane = await self._causal_store.get_memory_causal_lane(observation)
        causal = next(item for item in lane.operations if item.operation_id == observation.operation_id)
        self._audit.record_observation(
            operation_id=observation.operation_id.value,
            scope_reference=observation.scope_ref.scope_id.value,
            logical_provider_domain=observation.logical_provider_domain,
            binding_id=intent.provider_binding.binding_id.value,
            runtime_generation=intent.provider_binding.provider_generation,
            observation_sequence=causal.sequence,
            dispatch_certainty=result.durable_operation.state.value,
            causal_resolution=causal.resolution.value,
            history_complete=lane.state.history_complete,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return result
