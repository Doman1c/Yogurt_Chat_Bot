"""R3-A immutable provider-neutral recall contract coverage."""

from datetime import datetime, timezone

import pytest

from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.foundation.identifiers import (
    BindingId, CapabilityId, ConversationId, MemoryRecallQueryId, PersonaId,
    RuntimeInstanceId, TurnId, UserId,
)
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.memory.recall import (
    MEMORY_RECALL_CAPABILITY_ID, MemoryRecallBindingSnapshot, MemoryRecallCandidate,
    MemoryRecallDecision, MemoryRecallEvidence, MemoryRecallQuery,
    memory_conversation_scope_ref,
)


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
CONVERSATION = ConversationRef(
    persona_id=PersonaId(value="yogurt"), user_id=UserId(value="user"),
    conversation_id=ConversationId(value="conversation"),
)


def _binding(generation: int = 5) -> MemoryRecallBindingSnapshot:
    return MemoryRecallBindingSnapshot(
        binding_id=BindingId(value="memory-binding"), logical_provider_id="fake-memory",
        runtime_instance_id=RuntimeInstanceId(value="memory-runtime"), runtime_generation=generation,
        binding_revision="r1", memory_domain_id="personal-memory",
        capability_id=CapabilityId(value=MEMORY_RECALL_CAPABILITY_ID),
    )


def _query(message: str = "今天我要去东京") -> MemoryRecallQuery:
    return MemoryRecallQuery(
        recall_query_id=MemoryRecallQueryId(value="recall-query"), conversation_ref=CONVERSATION,
        turn_id=TurnId(value="turn"), memory_conversation_scope_ref=memory_conversation_scope_ref(CONVERSATION),
        normalized_user_message=message, current_input_fingerprint=RequestFingerprint(value="input-fingerprint"),
        max_items=3, token_budget=100, deadline=Deadline(expires_at=NOW),
        query_fingerprint=RequestFingerprint(value="query-fingerprint"),
    )


def _candidate(text: str = "过去的回忆", size=None) -> MemoryRecallCandidate:
    return MemoryRecallCandidate(
        stable_candidate_id="candidate", kind="memory", normalized_text=text, source_ref="source",
        content_fingerprint=RequestFingerprint(value="candidate-fingerprint"), advisory_score=0.5,
        content_size=len(text) if size is None else size,
    )


def test_scope_derivation_is_stable_and_has_no_surface_identity() -> None:
    first = memory_conversation_scope_ref(CONVERSATION)
    second = memory_conversation_scope_ref(CONVERSATION)
    assert first == second
    assert "origin_surface" not in MemoryRecallQuery.model_fields
    assert first.persona_id == "yogurt" and first.user_id == "user"


def test_query_and_candidate_enforce_bounded_normalized_data() -> None:
    with pytest.raises(ValueError):
        _query(" unnormalized ")
    with pytest.raises(ValueError):
        MemoryRecallQuery(
            recall_query_id=MemoryRecallQueryId(value="over-limit"), conversation_ref=CONVERSATION,
            turn_id=TurnId(value="turn"), memory_conversation_scope_ref=memory_conversation_scope_ref(CONVERSATION),
            normalized_user_message="hello", current_input_fingerprint=RequestFingerprint(value="input"),
            max_items=33, token_budget=100, deadline=Deadline(expires_at=NOW),
            query_fingerprint=RequestFingerprint(value="query"),
        )
    with pytest.raises(ValueError):
        _candidate(size=1)
    with pytest.raises(ValueError):
        MemoryRecallBindingSnapshot(
            binding_id=BindingId(value="binding"), logical_provider_id="fake", runtime_instance_id=RuntimeInstanceId(value="runtime"),
            runtime_generation=1, binding_revision="r1", memory_domain_id="domain", capability_id=CapabilityId(value="not-memory.recall"),
        )


def test_evidence_is_immutable_typed_and_provenance_bound() -> None:
    query = _query()
    evidence = MemoryRecallEvidence(
        recall_query_id=query.recall_query_id, conversation_ref=CONVERSATION, turn_id=query.turn_id,
        query_fingerprint=query.query_fingerprint, memory_conversation_scope_ref=query.memory_conversation_scope_ref,
        logical_provider_id="fake-memory", memory_domain_id="personal-memory", provider_binding_ref=_binding(),
        produced_by_generation=5, retrieved_at=NOW, decision=MemoryRecallDecision.AVAILABLE,
        candidates=(_candidate(),), advisory_token_estimate=1,
    )
    assert evidence.model_validate(evidence.model_dump()) == evidence
    with pytest.raises(ValueError):
        MemoryRecallEvidence(
            recall_query_id=query.recall_query_id, conversation_ref=CONVERSATION, turn_id=query.turn_id,
            query_fingerprint=query.query_fingerprint, memory_conversation_scope_ref=query.memory_conversation_scope_ref,
            logical_provider_id="other", memory_domain_id="personal-memory", provider_binding_ref=_binding(),
            produced_by_generation=5, retrieved_at=NOW, decision=MemoryRecallDecision.AVAILABLE,
            candidates=(_candidate(),), advisory_token_estimate=1,
        )
    with pytest.raises(ValueError):
        MemoryRecallEvidence.model_validate({**evidence.model_dump(), "bounded_provider_metadata": {"priority": "system"}})
