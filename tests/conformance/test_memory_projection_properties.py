from datetime import datetime, timezone

from hypothesis import given, strategies as st

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import ConversationId, OperationId, PersonaId, ReceiptId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.observations import CommittedContentObservation, PresentationObservation
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
