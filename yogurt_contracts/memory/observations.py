"""Public, immutable Memory observations. Committed content is never implicit user exposure."""

from typing import Optional, Union

from pydantic import model_validator

from ..conversation.refs import ConversationRef, TurnRef
from ..foundation.identifiers import InputId, OperationId, ReceiptId, ResponseId, SegmentId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..presentation.extent import MessagingPresentationExtent, TextPresentationExtent, VoicePresentationExtent
from ..presentation.facts import PresentationEvidenceLevel, presentation_evidence_rank


PresentationExtent = Union[TextPresentationExtent, VoicePresentationExtent, MessagingPresentationExtent]


class _ScopedMemoryObservation(FrozenContract):
    operation_id: OperationId
    conversation_ref: ConversationRef
    turn_ref: TurnRef
    observed_at: UtcInstant

    @model_validator(mode="after")
    def has_consistent_conversation_scope(self) -> "_ScopedMemoryObservation":
        if self.conversation_ref.conversation_id != self.turn_ref.conversation_id:
            raise ValueError("memory observation conversation_ref must match turn_ref")
        return self


class UserContentObservation(_ScopedMemoryObservation):
    input_id: Optional[InputId] = None
    content: str


class CommittedContentObservation(_ScopedMemoryObservation):
    """Semantic assistant output commit only; it carries no presentation or read assertion."""

    response_id: ResponseId
    segment_id: SegmentId
    content: str


class PresentationObservation(_ScopedMemoryObservation):
    response_id: ResponseId
    presentation_operation_id: OperationId
    receipt_id: ReceiptId
    evidence_level: PresentationEvidenceLevel
    segment_id: Optional[SegmentId] = None
    extent: Optional[PresentationExtent] = None

    @model_validator(mode="after")
    def has_correlated_extent(self) -> "PresentationObservation":
        if self.extent is None:
            return self
        if isinstance(self.extent, MessagingPresentationExtent):
            if presentation_evidence_rank(self.evidence_level) < presentation_evidence_rank(PresentationEvidenceLevel.DELIVERED):
                raise ValueError("messaging delivery extent requires delivered evidence")
            if self.segment_id is not None and self.segment_id not in self.extent.fully_delivered_segment_ids:
                raise ValueError("messaging extent must include the observation segment_id")
            return self
        if presentation_evidence_rank(self.evidence_level) < presentation_evidence_rank(PresentationEvidenceLevel.PRESENTED_TO_USER):
            raise ValueError("text or voice extent requires presented-to-user evidence")
        if self.segment_id is None or self.extent.segment_id != self.segment_id:
            raise ValueError("presentation observation segment_id must match its extent")
        return self
