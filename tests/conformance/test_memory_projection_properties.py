from datetime import datetime, timezone

from hypothesis import given, strategies as st

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import ConversationId, OperationId, PersonaId, ReceiptId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.observations import CommittedContentObservation, PresentationObservation
from yogurt_contracts.memory.deduplication import MemoryObservationDedupDecision, classify_memory_observation_retry
from yogurt_contracts.memory.projection import project_memory_exposure
from yogurt_contracts.presentation.extent import VoicePresentationExtent
from yogurt_contracts.presentation.facts import PresentationEvidenceLevel


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))
TURN = TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn"))


@given(st.integers(min_value=0, max_value=10000), st.integers(min_value=0, max_value=10000))
def test_voice_projection_is_monotonic_and_delivery_is_not_exposure(first: int, second: int) -> None:
    committed = CommittedContentObservation(operation_id=OperationId(value="commit"), conversation_ref=CONVERSATION, turn_ref=TURN, response_id=ResponseId(value="response"), segment_id=SegmentId(value="segment"), content="hello", observed_at=NOW)
    def observation(duration: int) -> PresentationObservation:
        return PresentationObservation(operation_id=OperationId(value="observation-" + str(duration)), conversation_ref=CONVERSATION, turn_ref=TURN, response_id=ResponseId(value="response"), presentation_operation_id=OperationId(value="present"), receipt_id=ReceiptId(value="receipt-" + str(duration)), evidence_level=PresentationEvidenceLevel.PRESENTED_TO_USER, segment_id=SegmentId(value="segment"), extent=VoicePresentationExtent(segment_id=SegmentId(value="segment"), played_duration_ms=duration), observed_at=NOW)
    projection = project_memory_exposure(committed, (observation(first), observation(second)))
    assert projection.voice_extent is not None and projection.voice_extent.played_duration_ms == max(first, second)


@given(st.sampled_from(("content", "observed_at", "response_id", "segment_id", "scope")))
def test_memory_observation_retry_rejects_every_public_payload_mutation(field: str) -> None:
    baseline = CommittedContentObservation(operation_id=OperationId(value="dedupe-operation"), conversation_ref=CONVERSATION, turn_ref=TURN, response_id=ResponseId(value="response"), segment_id=SegmentId(value="segment"), content="content", observed_at=NOW)
    assert classify_memory_observation_retry(baseline, baseline) == MemoryObservationDedupDecision.DUPLICATE_RETRY
    if field == "content":
        mutated = baseline.model_copy(update={"content": "changed"})
    elif field == "observed_at":
        mutated = baseline.model_copy(update={"observed_at": UtcInstant(value=datetime(2026, 1, 2, tzinfo=timezone.utc))})
    elif field == "response_id":
        mutated = baseline.model_copy(update={"response_id": ResponseId(value="other-response")})
    elif field == "segment_id":
        mutated = baseline.model_copy(update={"segment_id": SegmentId(value="other-segment")})
    else:
        other = ConversationRef(persona_id=PersonaId(value="other-persona"), user_id=UserId(value="other-user"), conversation_id=ConversationId(value="other-conversation"))
        mutated = baseline.model_copy(update={"conversation_ref": other, "turn_ref": TurnRef(conversation_id=other.conversation_id, turn_id=TURN.turn_id)})
    assert classify_memory_observation_retry(baseline, mutated) == MemoryObservationDedupDecision.PAYLOAD_CONFLICT
    assert classify_memory_observation_retry(baseline, baseline.model_copy(update={"operation_id": OperationId(value="other-operation")})) == MemoryObservationDedupDecision.DIFFERENT_OBSERVATION
