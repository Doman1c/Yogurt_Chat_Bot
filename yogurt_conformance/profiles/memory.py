"""Behavioral Memory scope, retry, and exposure conformance."""

from datetime import datetime, timezone

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import ConversationId, OperationId, PersonaId, ReceiptId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.deduplication import MemoryObservationDedupDecision
from yogurt_contracts.memory.observations import CommittedContentObservation, PresentationExtent, PresentationObservation
from yogurt_contracts.presentation.extent import TextOffsetUnit, TextPresentationExtent, VoicePresentationExtent
from yogurt_contracts.presentation.facts import PresentationEvidenceLevel
from yogurt_conformance.protocols.adapters import MemoryAdapterProtocol


def run_memory_conformance(adapter: MemoryAdapterProtocol) -> None:
    now = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
    conversation = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))
    turn = TurnRef(conversation_id=conversation.conversation_id, turn_id=TurnId(value="turn"))
    committed = CommittedContentObservation(operation_id=OperationId(value="commit"), conversation_ref=conversation, turn_ref=turn, response_id=ResponseId(value="response"), segment_id=SegmentId(value="segment"), content="酸奶", observed_at=now)
    assert adapter.store_observation(committed) == MemoryObservationDedupDecision.DIFFERENT_OBSERVATION
    assert adapter.store_observation(committed) == MemoryObservationDedupDecision.DUPLICATE_RETRY
    assert adapter.store_observation(committed.model_copy(update={"content": "changed"})) == MemoryObservationDedupDecision.PAYLOAD_CONFLICT
    assert not adapter.project_segment(committed, ()).is_fully_exposed
    def observed(name: str, extent: PresentationExtent, segment_id: SegmentId | None = None) -> PresentationObservation:
        return PresentationObservation(operation_id=OperationId(value="observation-" + name), conversation_ref=conversation, turn_ref=turn, response_id=committed.response_id, presentation_operation_id=OperationId(value="present"), receipt_id=ReceiptId(value="receipt-" + name), evidence_level=PresentationEvidenceLevel.PRESENTED_TO_USER, segment_id=segment_id or committed.segment_id, extent=extent, observed_at=now)
    byte_partial = observed("byte", TextPresentationExtent(segment_id=committed.segment_id, end_text_offset=3, offset_unit=TextOffsetUnit.UTF8_BYTE))
    codepoint_full = observed("codepoint", TextPresentationExtent(segment_id=committed.segment_id, end_text_offset=2))
    assert adapter.project_segment(committed, (byte_partial, codepoint_full)).is_fully_exposed
    assert adapter.project_segment(committed, (codepoint_full, byte_partial)).is_fully_exposed
    voice_a = observed("voice-a", VoicePresentationExtent(segment_id=committed.segment_id, played_duration_ms=1000, end_text_offset=2))
    voice_b = observed("voice-b", VoicePresentationExtent(segment_id=committed.segment_id, played_duration_ms=2000, end_text_offset=1))
    voice = adapter.project_segment(committed, (voice_a, voice_b)).voice_extent
    assert voice is not None and voice.played_duration_ms == 2000 and voice.end_text_offset == 2
    other = observed("other", TextPresentationExtent(segment_id=SegmentId(value="other"), end_text_offset=2), SegmentId(value="other"))
    assert adapter.project_segment(committed, (other,)).highest_evidence is None
