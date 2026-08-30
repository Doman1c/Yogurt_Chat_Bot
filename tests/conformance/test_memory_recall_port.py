"""R3-A dispatch conformance: invalid requests never invoke the provider fake."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from yogurt_conformance.fakes.memory_recall import FakeMemoryRecallPort
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.execution import CapabilityAuthorizationSnapshot
from yogurt_contracts.foundation.identifiers import (
    BindingId, CapabilityId, ConversationId, MemoryRecallQueryId, PersonaId,
    RuntimeInstanceId, TurnId, UserId,
)
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.identity.authority import AuthorityClaims
from yogurt_contracts.memory.recall import (
    MEMORY_RECALL_CAPABILITY_ID, MemoryRecallBindingSnapshot, MemoryRecallCallOutcome,
    MemoryRecallCandidate, MemoryRecallCapabilityState, MemoryRecallDecision,
    MemoryRecallEvidence, MemoryRecallProviderResult, MemoryRecallQuery, RecallEligibility,
    memory_conversation_scope_ref,
)
from yogurt_core.memory.recall_policy import MemoryRecallDispatcher


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
FUTURE = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=5))
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))


def _binding(generation: int = 5) -> MemoryRecallBindingSnapshot:
    return MemoryRecallBindingSnapshot(binding_id=BindingId(value="binding"), logical_provider_id="fake", runtime_instance_id=RuntimeInstanceId(value="provider"), runtime_generation=generation, binding_revision="r1", memory_domain_id="personal", capability_id=CapabilityId(value=MEMORY_RECALL_CAPABILITY_ID))


def _query(deadline: UtcInstant = FUTURE) -> MemoryRecallQuery:
    return MemoryRecallQuery(recall_query_id=MemoryRecallQueryId(value="query"), conversation_ref=CONVERSATION, turn_id=TurnId(value="turn"), memory_conversation_scope_ref=memory_conversation_scope_ref(CONVERSATION), normalized_user_message="hello", current_input_fingerprint=RequestFingerprint(value="input"), max_items=2, token_budget=20, deadline=Deadline(expires_at=deadline), query_fingerprint=RequestFingerprint(value="query-fingerprint"))


def _authorization(binding: MemoryRecallBindingSnapshot, valid_until: UtcInstant = FUTURE, scope: str = MEMORY_RECALL_CAPABILITY_ID) -> CapabilityAuthorizationSnapshot:
    claims = AuthorityClaims(actor=RuntimeInstanceId(value="core"), subject=CONVERSATION.user_id, origin="core", audience=binding.runtime_instance_id, scope=CapabilityId(value=scope), grant_revision="grant", valid_until=valid_until, authority_reference="authority")
    return CapabilityAuthorizationSnapshot(claims=claims, authorization_revision="auth-r1", checked_at=NOW, valid_until=valid_until)


def _evidence(query: MemoryRecallQuery, binding: MemoryRecallBindingSnapshot) -> MemoryRecallEvidence:
    candidate = MemoryRecallCandidate(stable_candidate_id="candidate", kind="memory", normalized_text="older memory", source_ref="source", content_fingerprint=RequestFingerprint(value="candidate"), advisory_score=0.9, content_size=12)
    return MemoryRecallEvidence(recall_query_id=query.recall_query_id, conversation_ref=query.conversation_ref, turn_id=query.turn_id, query_fingerprint=query.query_fingerprint, memory_conversation_scope_ref=query.memory_conversation_scope_ref, logical_provider_id=binding.logical_provider_id, memory_domain_id=binding.memory_domain_id, provider_binding_ref=binding, produced_by_generation=binding.runtime_generation, retrieved_at=NOW, decision=MemoryRecallDecision.AVAILABLE, candidates=(candidate,), advisory_token_estimate=1)


def _fake(state: MemoryRecallCapabilityState = MemoryRecallCapabilityState.AVAILABLE, binding=None, result=None):
    current = _binding() if binding is None else binding
    query = _query()
    response = MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.SUCCESS, evidence=_evidence(query, current)) if result is None else result
    return FakeMemoryRecallPort(state, current, response)


@pytest.mark.parametrize("precondition", [RecallEligibility.OBSERVATION_NOT_CONFIRMED, RecallEligibility.PRIOR_CAUSAL_OBSERVATION_PENDING, RecallEligibility.HISTORY_INCOMPLETE, RecallEligibility.INVALID_SCOPE])
def test_not_eligible_never_invokes_provider(precondition: RecallEligibility) -> None:
    binding = _binding()
    fake = _fake(binding=binding)
    result = asyncio.run(MemoryRecallDispatcher(fake).recall(_query(), binding, _authorization(binding), NOW, precondition))
    assert result.eligibility == precondition and result.call_outcome is None and fake.call_count == 0


@pytest.mark.parametrize("case", ["unavailable", "deadline", "authorization", "stale_binding"])
def test_pre_dispatch_failures_are_separate_and_do_not_call_provider(case: str) -> None:
    binding = _binding()
    if case == "unavailable":
        fake, query, auth = _fake(state=MemoryRecallCapabilityState.UNAVAILABLE, binding=binding), _query(), _authorization(binding)
    elif case == "deadline":
        fake, query, auth = _fake(binding=binding), _query(NOW), _authorization(binding)
        now = FUTURE
        result = asyncio.run(MemoryRecallDispatcher(fake).recall(query, binding, auth, now))
        assert result.eligibility == RecallEligibility.DEADLINE_EXHAUSTED and result.call_outcome is None and fake.call_count == 0
        return
    elif case == "authorization":
        fake, query, auth = _fake(binding=binding), _query(), _authorization(binding, scope="wrong.scope")
    else:
        fake, query, auth = _fake(binding=_binding(6)), _query(), _authorization(binding)
    result = asyncio.run(MemoryRecallDispatcher(fake).recall(query, binding, auth, NOW))
    expected = RecallEligibility.CAPABILITY_UNAVAILABLE if case == "unavailable" else (RecallEligibility.UNAUTHORIZED if case == "authorization" else RecallEligibility.STALE_BINDING)
    assert result.eligibility == expected and result.call_outcome is None and fake.call_count == 0


@pytest.mark.parametrize("outcome", [MemoryRecallCallOutcome.UNAVAILABLE, MemoryRecallCallOutcome.DEADLINE_EXCEEDED, MemoryRecallCallOutcome.INVALID_PROVIDER_RESPONSE])
def test_provider_failures_are_call_outcomes_not_empty_or_blocked(outcome: MemoryRecallCallOutcome) -> None:
    binding = _binding()
    fake = _fake(binding=binding, result=MemoryRecallProviderResult(call_outcome=outcome))
    result = asyncio.run(MemoryRecallDispatcher(fake).recall(_query(), binding, _authorization(binding), NOW))
    assert result.eligibility == RecallEligibility.ELIGIBLE
    assert result.call_outcome == outcome and result.evidence is None and fake.call_count == 1


@pytest.mark.parametrize("decision", [MemoryRecallDecision.EMPTY, MemoryRecallDecision.SKIPPED, MemoryRecallDecision.BLOCKED])
def test_successful_semantic_decisions_are_evidence_not_provider_unavailability(decision: MemoryRecallDecision) -> None:
    binding, query = _binding(), _query()
    semantic_evidence = MemoryRecallEvidence(recall_query_id=query.recall_query_id, conversation_ref=query.conversation_ref, turn_id=query.turn_id, query_fingerprint=query.query_fingerprint, memory_conversation_scope_ref=query.memory_conversation_scope_ref, logical_provider_id=binding.logical_provider_id, memory_domain_id=binding.memory_domain_id, provider_binding_ref=binding, produced_by_generation=5, retrieved_at=NOW, decision=decision, candidates=(), advisory_token_estimate=0)
    fake = _fake(binding=binding, result=MemoryRecallProviderResult(call_outcome=MemoryRecallCallOutcome.SUCCESS, evidence=semantic_evidence))
    result = asyncio.run(MemoryRecallDispatcher(fake).recall(query, binding, _authorization(binding), NOW))
    assert result.call_outcome == MemoryRecallCallOutcome.SUCCESS and result.evidence is not None
    assert result.evidence.decision == decision and fake.call_count == 1
