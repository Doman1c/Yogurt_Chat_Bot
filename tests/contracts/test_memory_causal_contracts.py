"""R3-B semantic observation and causal-state contract tests."""

from datetime import datetime, timezone

import pytest

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import (
    ConversationId, InputId, OperationId, PersonaId, TurnId, UserId,
)
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.causal import (
    MemoryCausalLaneSnapshot, MemoryCausalOperation, MemoryCausalResolution, MemoryCausalState,
    MemoryObservationOperation, MemoryObservationParticipation, memory_observation_fingerprint,
    memory_observation_participation,
)
from yogurt_contracts.memory.observations import CommittedContentObservation, PresentationObservation, UserContentObservation
from yogurt_contracts.memory.recall import memory_conversation_scope_ref
from yogurt_contracts.foundation.identifiers import ReceiptId, ResponseId, SegmentId
from yogurt_contracts.presentation.facts import PresentationEvidenceLevel


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))


def _user(operation: str = "operation", content: str = "hello") -> UserContentObservation:
    return UserContentObservation(operation_id=OperationId(value=operation), conversation_ref=CONVERSATION, turn_ref=TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn")), input_id=InputId(value="input"), content=content, observed_at=NOW)


def test_only_user_content_participates_in_legacy_memory_lane() -> None:
    assert memory_observation_participation(_user()) == MemoryObservationParticipation.PARTICIPATES
    committed = CommittedContentObservation(operation_id=OperationId(value="committed"), conversation_ref=CONVERSATION, turn_ref=TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn")), response_id=ResponseId(value="response"), segment_id=SegmentId(value="segment"), content="assistant", observed_at=NOW)
    assert memory_observation_participation(committed) == MemoryObservationParticipation.DOES_NOT_PARTICIPATE
    presentation = PresentationObservation(operation_id=OperationId(value="presented"), conversation_ref=CONVERSATION, turn_ref=TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn")), response_id=ResponseId(value="response"), presentation_operation_id=OperationId(value="present"), receipt_id=ReceiptId(value="receipt"), evidence_level=PresentationEvidenceLevel.DELIVERED, observed_at=NOW)
    assert memory_observation_participation(presentation) == MemoryObservationParticipation.DOES_NOT_PARTICIPATE


def test_semantic_observation_fingerprint_is_provider_independent_and_bound() -> None:
    observation = _user()
    operation = MemoryObservationOperation(operation_id=observation.operation_id, observation=observation, scope_ref=memory_conversation_scope_ref(CONVERSATION), logical_provider_domain="legacy-v5", observation_fingerprint=memory_observation_fingerprint(observation))
    assert operation.observation_fingerprint == memory_observation_fingerprint(observation)
    with pytest.raises(ValueError):
        MemoryObservationOperation(operation_id=observation.operation_id, observation=observation, scope_ref=memory_conversation_scope_ref(CONVERSATION), logical_provider_domain="legacy-v5", observation_fingerprint=memory_observation_fingerprint(_user(content="changed")))


def test_lane_snapshot_requires_continuity_and_derived_watermarks() -> None:
    scope = memory_conversation_scope_ref(CONVERSATION)
    state = MemoryCausalState(scope_ref=scope, logical_provider_domain="legacy-v5", next_sequence=3, resolved_through_sequence=1, applied_through_sequence=1, revision=3)
    one = MemoryCausalOperation(operation_id=OperationId(value="one"), sequence=1, observation_fingerprint=memory_observation_fingerprint(_user("one")), resolution=MemoryCausalResolution.APPLIED)
    two = MemoryCausalOperation(operation_id=OperationId(value="two"), sequence=2, observation_fingerprint=memory_observation_fingerprint(_user("two")))
    assert MemoryCausalLaneSnapshot(state=state, operations=(one, two)).state.next_sequence == 3
    with pytest.raises(ValueError):
        MemoryCausalLaneSnapshot(state=state, operations=(two,))
