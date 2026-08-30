"""R3-B deterministic provider-neutral causal lane behavior."""

from datetime import datetime, timezone

import pytest

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import ConversationId, InputId, OperationId, PersonaId, TurnId, UserId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.causal import (
    MemoryCausalResolution, MemoryObservationOperation, memory_observation_fingerprint,
)
from yogurt_contracts.memory.observations import CommittedContentObservation, UserContentObservation
from yogurt_contracts.memory.recall import RecallEligibility, memory_conversation_scope_ref
from yogurt_contracts.foundation.identifiers import ResponseId, SegmentId
from yogurt_conformance.fakes.memory_observation import FakeOrderedMemoryProvider
from yogurt_core.memory.causal_lane import MemoryCausalLane, MemoryCausalLaneError, MemoryObservationSequencer


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))


def _semantic(operation: str, domain: str = "legacy-v5", content: str = "hello") -> MemoryObservationOperation:
    observation = UserContentObservation(operation_id=OperationId(value=operation), conversation_ref=CONVERSATION, turn_ref=TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn-" + operation)), input_id=InputId(value="input-" + operation), content=content, observed_at=NOW)
    return MemoryObservationOperation(operation_id=observation.operation_id, observation=observation, scope_ref=memory_conversation_scope_ref(CONVERSATION), logical_provider_domain=domain, observation_fingerprint=memory_observation_fingerprint(observation))


def _lane() -> MemoryCausalLane:
    return MemoryCausalLane.empty(_semantic("one"))


def test_sequence_is_core_ordered_and_duplicate_is_idempotent() -> None:
    lane, one, two = _lane(), _semantic("one"), _semantic("two")
    first = lane.allocate(one)
    assert first.sequence == 1 and lane.allocate(one) == first
    assert lane.allocate(two).sequence == 2
    with pytest.raises(MemoryCausalLaneError, match="MEMORY_OPERATION_ID_CONFLICT"):
        lane.allocate(_semantic("one", content="conflict"))


def test_unsupported_observation_does_not_create_a_sequence_hole() -> None:
    lane = _lane()
    sequencer = MemoryObservationSequencer(lane)
    unsupported = CommittedContentObservation(
        operation_id=OperationId(value="assistant"), conversation_ref=CONVERSATION,
        turn_ref=TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="assistant-turn")),
        response_id=ResponseId(value="response"), segment_id=SegmentId(value="segment"), content="assistant", observed_at=NOW,
    )
    assert sequencer.register(unsupported) is None
    assert sequencer.register(_semantic("one").observation, _semantic("one")).sequence == 1


def test_resolved_not_applied_continues_lane_but_permanently_marks_history_incomplete() -> None:
    lane, one, two, three = _lane(), _semantic("one"), _semantic("two"), _semantic("three")
    first, second, third = lane.allocate(one), lane.allocate(two), lane.allocate(three)
    lane.resolve(first.operation_id, MemoryCausalResolution.RESOLVED_NOT_APPLIED)
    assert lane.can_dispatch(second.operation_id)
    lane.resolve(second.operation_id, MemoryCausalResolution.APPLIED)
    assert lane.can_dispatch(third.operation_id)
    lane.resolve(third.operation_id, MemoryCausalResolution.APPLIED)
    assert lane.state.resolved_through_sequence == 3
    assert not lane.state.history_complete
    assert lane.recall_eligibility() == RecallEligibility.HISTORY_INCOMPLETE


def test_unresolved_blocks_later_application_until_same_domain_reconciliation() -> None:
    lane, one, two = _lane(), _semantic("one"), _semantic("two")
    first, second = lane.allocate(one), lane.allocate(two)
    lane.resolve(first.operation_id, MemoryCausalResolution.UNRESOLVED)
    assert not lane.can_dispatch(second.operation_id)
    assert lane.recall_eligibility() == RecallEligibility.HISTORY_INCOMPLETE
    lane.reconcile_same_domain(one, MemoryCausalResolution.APPLIED)
    assert lane.can_dispatch(second.operation_id)


def test_fake_provider_applies_only_in_causal_order() -> None:
    lane, one, two = _lane(), _semantic("one"), _semantic("two")
    first, second = lane.allocate(one), lane.allocate(two)
    provider = FakeOrderedMemoryProvider("legacy-v5")
    assert provider.apply(lane, second) is False
    assert provider.apply(lane, first) is True
    assert provider.apply(lane, second) is True
    assert provider.applied_sequences == (1, 2)


def test_restart_snapshot_preserves_incomplete_history_and_cross_domain_takeover_is_rejected() -> None:
    lane, one = _lane(), _semantic("one")
    causal = lane.allocate(one)
    lane.resolve(causal.operation_id, MemoryCausalResolution.RESOLVED_NOT_APPLIED)
    restarted = MemoryCausalLane(lane.snapshot())
    assert not restarted.state.history_complete
    with pytest.raises(MemoryCausalLaneError, match="CROSS_DOMAIN_UNCERTAINTY_REJECTED"):
        restarted.reconcile_same_domain(_semantic("one", domain="other-domain"), MemoryCausalResolution.APPLIED)
