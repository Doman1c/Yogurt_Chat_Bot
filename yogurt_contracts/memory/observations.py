from typing import Optional, Union

from ..conversation.refs import ConversationRef, TurnRef
from ..foundation.identifiers import InputId, OperationId, ReceiptId, ResponseId, SegmentId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..presentation.extent import MessagingPresentationExtent, TextPresentationExtent, VoicePresentationExtent
from ..presentation.facts import PresentationEvidenceLevel


class UserContentObservation(FrozenContract):
    operation_id: OperationId
    conversation_ref: ConversationRef
    turn_ref: TurnRef
    input_id: Optional[InputId] = None
    content: str
    observed_at: UtcInstant


class CommittedContentObservation(FrozenContract):
    operation_id: OperationId
    conversation_ref: ConversationRef
    turn_ref: TurnRef
    response_id: ResponseId
    segment_id: Optional[SegmentId] = None
    speaker: str
    content: str
    observed_at: UtcInstant


class PresentationObservation(FrozenContract):
    operation_id: OperationId
    conversation_ref: ConversationRef
    turn_ref: TurnRef
    response_id: ResponseId
    segment_id: Optional[SegmentId] = None
    presentation_operation_id: OperationId
    receipt_id: ReceiptId
    evidence_level: PresentationEvidenceLevel
    extent: Optional[Union[TextPresentationExtent, VoicePresentationExtent, MessagingPresentationExtent]] = None
    observed_at: UtcInstant
