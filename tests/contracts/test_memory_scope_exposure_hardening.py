"""P0-05 public Memory scope, exposure-evidence, projection, and retry regressions."""

from datetime import datetime, timezone

import pytest

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.foundation.identifiers import ConversationId, InputId, OperationId, PersonaId, ReceiptId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.memory.deduplication import MemoryObservationDedupDecision, classify_memory_observation_retry
from yogurt_contracts.memory.observations import CommittedContentObservation, PresentationObservation, UserContentObservation
from yogurt_contracts.memory.projection import project_memory_exposure
from yogurt_contracts.presentation.extent import MessagingPresentationExtent, TextOffsetUnit, TextPresentationExtent, VoicePresentationExtent
from yogurt_contracts.presentation.facts import PresentationEvidenceLevel


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))
TURN = TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn"))


def _committed(content: str = "hello world", segment: str = "segment") -> CommittedContentObservation:
    return CommittedContentObservation(operation_id=OperationId(value="commit-operation"), conversation_ref=CONVERSATION, turn_ref=TURN, response_id=ResponseId(value="response"), segment_id=SegmentId(value=segment), content=content, observed_at=NOW)


def _presentation(level: PresentationEvidenceLevel, extent=None, segment: str = "segment") -> PresentationObservation:
    return PresentationObservation(operation_id=OperationId(value="observation-" + level.value + "-" + segment), conversation_ref=CONVERSATION, turn_ref=TURN, response_id=ResponseId(value="response"), presentation_operation_id=OperationId(value="presentation-operation"), receipt_id=ReceiptId(value="receipt-" + level.value + "-" + segment), evidence_level=level, segment_id=SegmentId(value=segment), extent=extent, observed_at=NOW)


def test_observations_make_persona_user_conversation_and_turn_scope_public() -> None:
    observation = UserContentObservation(operation_id=OperationId(value="user-operation"), conversation_ref=CONVERSATION, turn_ref=TURN, input_id=InputId(value="input"), content="hello", observed_at=NOW)
    assert observation.conversation_ref.persona_id == PersonaId(value="persona")
    assert observation.conversation_ref.user_id == UserId(value="user")
    assert observation.conversation_ref.conversation_id == ConversationId(value="conversation")
    assert observation.turn_ref.turn_id == TurnId(value="turn")
    mismatched_turn = TurnRef(conversation_id=ConversationId(value="other"), turn_id=TurnId(value="turn"))
    with pytest.raises(ValueError):
        UserContentObservation(operation_id=OperationId(value="bad"), conversation_ref=CONVERSATION, turn_ref=mismatched_turn, content="hello", observed_at=NOW)


def test_committed_content_without_presentation_has_unknown_no_exposure_evidence() -> None:
    projection = project_memory_exposure(_committed(), ())
    assert projection.committed_content
    assert projection.highest_evidence is None
    assert not projection.is_partially_exposed
    assert not projection.is_fully_exposed


def test_evidence_levels_do_not_automatically_upgrade_to_presented_or_read() -> None:
    committed = _committed()
    accepted = _presentation(PresentationEvidenceLevel.PROVIDER_ACCEPTED)
    delivered = _presentation(PresentationEvidenceLevel.DELIVERED)
    projection = project_memory_exposure(committed, (accepted, delivered))
    assert projection.highest_evidence == PresentationEvidenceLevel.DELIVERED
    assert projection.highest_evidence != PresentationEvidenceLevel.PRESENTED_TO_USER
    assert projection.highest_evidence != PresentationEvidenceLevel.READ_BY_USER


def test_partial_voice_and_text_extents_never_imply_full_exposure() -> None:
    committed = _committed()
    voice = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, VoicePresentationExtent(segment_id=SegmentId(value="segment"), played_duration_ms=800))
    partial_voice = project_memory_exposure(committed, (voice,))
    assert partial_voice.is_partially_exposed and not partial_voice.is_fully_exposed
    text = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, TextPresentationExtent(segment_id=SegmentId(value="segment"), end_text_offset=7))
    partial_text = project_memory_exposure(committed, (text,))
    assert partial_text.is_partially_exposed and not partial_text.is_fully_exposed


def test_projection_is_monotonic_for_evidence_and_extent() -> None:
    committed = _committed()
    high = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, VoicePresentationExtent(segment_id=SegmentId(value="segment"), played_duration_ms=700))
    stale = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, VoicePresentationExtent(segment_id=SegmentId(value="segment"), played_duration_ms=400))
    lower_evidence = _presentation(PresentationEvidenceLevel.DELIVERED, MessagingPresentationExtent(fully_delivered_segment_ids=(SegmentId(value="segment"),)))
    projection = project_memory_exposure(committed, (high, stale, lower_evidence))
    assert projection.highest_evidence == PresentationEvidenceLevel.PRESENTED_TO_USER
    assert projection.voice_extent is not None and projection.voice_extent.played_duration_ms == 700


def test_messaging_delivery_tracks_only_known_segments_and_is_not_read_evidence() -> None:
    committed = _committed(segment="a")
    delivery = _presentation(PresentationEvidenceLevel.DELIVERED, MessagingPresentationExtent(fully_delivered_segment_ids=(SegmentId(value="a"), SegmentId(value="b"))), segment="a")
    projection = project_memory_exposure(committed, (delivery,))
    assert projection.messaging_extent is not None
    assert projection.messaging_extent.fully_delivered_segment_ids == (SegmentId(value="a"), SegmentId(value="b"))
    assert projection.highest_evidence == PresentationEvidenceLevel.DELIVERED
    assert not projection.is_partially_exposed
    assert not projection.is_fully_exposed


def test_offset_units_and_segment_projection_are_exposure_safe() -> None:
    unicode = _committed(content="é")
    codepoint = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, TextPresentationExtent(segment_id=SegmentId(value="segment"), end_text_offset=1))
    byte_partial = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, TextPresentationExtent(segment_id=SegmentId(value="segment"), end_text_offset=1, offset_unit=TextOffsetUnit.UTF8_BYTE))
    byte_full = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, TextPresentationExtent(segment_id=SegmentId(value="segment"), end_text_offset=2, offset_unit=TextOffsetUnit.UTF8_BYTE))
    assert project_memory_exposure(unicode, (codepoint,)).is_fully_exposed
    assert project_memory_exposure(unicode, (byte_partial,)).is_partially_exposed
    assert project_memory_exposure(unicode, (byte_full,)).is_fully_exposed
    response_level = PresentationObservation(operation_id=OperationId(value="response-level"), conversation_ref=CONVERSATION, turn_ref=TURN, response_id=ResponseId(value="response"), presentation_operation_id=OperationId(value="presentation"), receipt_id=ReceiptId(value="response-receipt"), evidence_level=PresentationEvidenceLevel.PRESENTED_TO_USER, observed_at=NOW)
    assert project_memory_exposure(unicode, (response_level,)).highest_evidence is None


def test_messaging_extent_uniqueness_and_retry_timestamp_are_authoritative() -> None:
    with pytest.raises(ValueError):
        MessagingPresentationExtent(fully_delivered_segment_ids=(SegmentId(value="a"), SegmentId(value="a")))
    with pytest.raises(ValueError):
        MessagingPresentationExtent(fully_delivered_segment_ids=(SegmentId(value="a"),), external_message_refs=("ref", "ref"))
    committed = _committed()
    changed_time = committed.model_copy(update={"observed_at": UtcInstant(value=datetime(2026, 1, 2, tzinfo=timezone.utc))})
    assert classify_memory_observation_retry(committed, changed_time) == MemoryObservationDedupDecision.PAYLOAD_CONFLICT


def test_observation_round_trips_and_retry_deduplication_is_payload_safe() -> None:
    user = UserContentObservation(operation_id=OperationId(value="user-operation"), conversation_ref=CONVERSATION, turn_ref=TURN, input_id=InputId(value="input"), content="hello", observed_at=NOW)
    committed = _committed()
    presentation = _presentation(PresentationEvidenceLevel.PRESENTED_TO_USER, TextPresentationExtent(segment_id=SegmentId(value="segment"), end_text_offset=5))
    assert UserContentObservation.model_validate(user.model_dump()) == user
    assert CommittedContentObservation.model_validate(committed.model_dump()) == committed
    assert PresentationObservation.model_validate(presentation.model_dump()) == presentation
    assert classify_memory_observation_retry(committed, committed) == MemoryObservationDedupDecision.DUPLICATE_RETRY
    conflicting = committed.model_copy(update={"content": "changed"})
    assert classify_memory_observation_retry(committed, conflicting) == MemoryObservationDedupDecision.PAYLOAD_CONFLICT
