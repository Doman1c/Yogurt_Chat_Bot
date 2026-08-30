"""R3-A dispatch and admission policy, deliberately detached from prompt composition."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from yogurt_contracts.execution import CapabilityAuthorizationSnapshot
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.recall import (
    MEMORY_RECALL_CAPABILITY_ID,
    MemoryRecallBindingSnapshot,
    MemoryRecallCapabilityState,
    MemoryRecallCandidate,
    MemoryRecallDispatchResult,
    MemoryRecallEvidence,
    MemoryRecallQuery,
    RecallEligibility,
    normalize_memory_text,
)

from yogurt_core.ports.memory_recall import MemoryRecallPort


class MemoryContextAdmissionStatus(str, Enum):
    ADMITTED = "admitted"
    FILTERED_EMPTY = "filtered_empty"
    REJECTED = "rejected"


class MemoryContextRejectReason(str, Enum):
    RECALL_QUERY_ID_MISMATCH = "recall_query_id_mismatch"
    CONVERSATION_REF_MISMATCH = "conversation_ref_mismatch"
    TURN_ID_MISMATCH = "turn_id_mismatch"
    QUERY_FINGERPRINT_MISMATCH = "query_fingerprint_mismatch"
    MEMORY_SCOPE_MISMATCH = "memory_scope_mismatch"
    PROVIDER_PROVENANCE_MISMATCH = "provider_provenance_mismatch"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    DEADLINE_EXPIRED = "deadline_expired"
    CURRENT_TURN_SELF_RECALL = "current_turn_self_recall"
    SELF_RECALL_FILTERED_EMPTY = "self_recall_filtered_empty"
    CONTEXT_BUDGET_EXHAUSTED = "context_budget_exhausted"


class MemoryTrustBoundary(str, Enum):
    UNTRUSTED_SEMANTIC_DATA = "untrusted_semantic_data"


@dataclass(frozen=True)
class AdmittedMemoryCandidate:
    """Data for a future composer, with no role or capability-bearing fields."""

    text: str
    source_ref: str
    trust_boundary: MemoryTrustBoundary = MemoryTrustBoundary.UNTRUSTED_SEMANTIC_DATA


@dataclass(frozen=True)
class MemoryContextAdmission:
    status: MemoryContextAdmissionStatus
    reason: Optional[MemoryContextRejectReason]
    candidates: Tuple[AdmittedMemoryCandidate, ...] = ()


class MemoryRecallDispatcher:
    """Stops invalid calls before they can reach a removable provider."""

    def __init__(self, port: MemoryRecallPort) -> None:
        self._port = port

    async def recall(
        self,
        query: MemoryRecallQuery,
        expected_binding: MemoryRecallBindingSnapshot,
        authorization: CapabilityAuthorizationSnapshot,
        now: UtcInstant,
        precondition: RecallEligibility = RecallEligibility.ELIGIBLE,
    ) -> MemoryRecallDispatchResult:
        if precondition != RecallEligibility.ELIGIBLE:
            return MemoryRecallDispatchResult(
                capability_state=MemoryRecallCapabilityState.AVAILABLE,
                eligibility=precondition,
            )
        state = await self._port.capability_state()
        if state != MemoryRecallCapabilityState.AVAILABLE:
            return MemoryRecallDispatchResult(capability_state=state, eligibility=RecallEligibility.CAPABILITY_UNAVAILABLE)
        if now.value > query.deadline.expires_at.value:
            return MemoryRecallDispatchResult(capability_state=state, eligibility=RecallEligibility.DEADLINE_EXHAUSTED)
        if not self._authorization_matches(query, expected_binding, authorization, now):
            return MemoryRecallDispatchResult(capability_state=state, eligibility=RecallEligibility.UNAUTHORIZED)
        current = await self._port.current_binding()
        if current != expected_binding:
            return MemoryRecallDispatchResult(capability_state=state, eligibility=RecallEligibility.STALE_BINDING)
        result = await self._port.recall(query, expected_binding, authorization)
        return MemoryRecallDispatchResult(
            capability_state=state,
            eligibility=RecallEligibility.ELIGIBLE,
            call_outcome=result.call_outcome,
            evidence=result.evidence,
        )

    @staticmethod
    def _authorization_matches(
        query: MemoryRecallQuery,
        binding: MemoryRecallBindingSnapshot,
        authorization: CapabilityAuthorizationSnapshot,
        now: UtcInstant,
    ) -> bool:
        claims = authorization.claims
        return (
            claims.scope.value == MEMORY_RECALL_CAPABILITY_ID
            and claims.subject == query.conversation_ref.user_id
            and claims.audience == binding.runtime_instance_id
            and now.value <= authorization.valid_until.value
            and now.value <= claims.valid_until.value
        )


class MemoryRecallAdmissionPolicy:
    """Correlates historical evidence before exposing bounded untrusted data to context."""

    def __init__(self, max_context_characters: int = 4096) -> None:
        if max_context_characters < 1:
            raise ValueError("max_context_characters must be positive")
        self._max_context_characters = max_context_characters

    def admit(
        self,
        query: MemoryRecallQuery,
        evidence: MemoryRecallEvidence,
        dispatch_binding: MemoryRecallBindingSnapshot,
        authorization: CapabilityAuthorizationSnapshot,
        now: UtcInstant,
    ) -> MemoryContextAdmission:
        mismatch = self._correlation_mismatch(query, evidence, dispatch_binding)
        if mismatch is not None:
            return MemoryContextAdmission(MemoryContextAdmissionStatus.REJECTED, mismatch)
        if now.value > authorization.valid_until.value or now.value > authorization.claims.valid_until.value:
            return MemoryContextAdmission(MemoryContextAdmissionStatus.REJECTED, MemoryContextRejectReason.AUTHORIZATION_EXPIRED)
        if now.value > query.deadline.expires_at.value:
            return MemoryContextAdmission(MemoryContextAdmissionStatus.REJECTED, MemoryContextRejectReason.DEADLINE_EXPIRED)

        remaining = min(self._max_context_characters, query.token_budget * 4)
        admitted = []  # type: list[AdmittedMemoryCandidate]
        saw_self_recall = False
        for candidate in evidence.candidates:
            if self._is_current_turn_self_recall(query, candidate):
                saw_self_recall = True
                continue
            if candidate.content_size > remaining:
                continue
            admitted.append(AdmittedMemoryCandidate(text=candidate.normalized_text, source_ref=candidate.source_ref))
            remaining -= candidate.content_size
        if admitted:
            return MemoryContextAdmission(MemoryContextAdmissionStatus.ADMITTED, None, tuple(admitted))
        if saw_self_recall:
            return MemoryContextAdmission(MemoryContextAdmissionStatus.FILTERED_EMPTY, MemoryContextRejectReason.SELF_RECALL_FILTERED_EMPTY)
        return MemoryContextAdmission(MemoryContextAdmissionStatus.FILTERED_EMPTY, MemoryContextRejectReason.CONTEXT_BUDGET_EXHAUSTED)

    @staticmethod
    def _correlation_mismatch(
        query: MemoryRecallQuery,
        evidence: MemoryRecallEvidence,
        binding: MemoryRecallBindingSnapshot,
    ) -> Optional[MemoryContextRejectReason]:
        if evidence.recall_query_id != query.recall_query_id:
            return MemoryContextRejectReason.RECALL_QUERY_ID_MISMATCH
        if evidence.conversation_ref != query.conversation_ref:
            return MemoryContextRejectReason.CONVERSATION_REF_MISMATCH
        if evidence.turn_id != query.turn_id:
            return MemoryContextRejectReason.TURN_ID_MISMATCH
        if evidence.query_fingerprint != query.query_fingerprint:
            return MemoryContextRejectReason.QUERY_FINGERPRINT_MISMATCH
        if evidence.memory_conversation_scope_ref != query.memory_conversation_scope_ref:
            return MemoryContextRejectReason.MEMORY_SCOPE_MISMATCH
        if evidence.provider_binding_ref != binding:
            return MemoryContextRejectReason.PROVIDER_PROVENANCE_MISMATCH
        return None

    @staticmethod
    def _is_current_turn_self_recall(query: MemoryRecallQuery, candidate: MemoryRecallCandidate) -> bool:
        if candidate.content_fingerprint == query.current_input_fingerprint:
            return True
        if candidate.source_turn_id == query.turn_id:
            return True
        if query.current_observation_id is not None and candidate.source_observation_id == query.current_observation_id:
            return True
        return normalize_memory_text(candidate.normalized_text) == normalize_memory_text(query.normalized_user_message)
