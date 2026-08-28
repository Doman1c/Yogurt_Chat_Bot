from typing import Optional

from ..conversation.refs import TurnRef
from ..foundation.identifiers import OperationId, ResponseId, SegmentId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant


class UserContentObservation(FrozenContract):
    operation_id: OperationId
    turn_ref: TurnRef
    content: str
    observed_at: UtcInstant


class CommittedContentObservation(FrozenContract):
    operation_id: OperationId
    turn_ref: TurnRef
    response_id: Optional[ResponseId] = None
    segment_id: Optional[SegmentId] = None
    speaker: str
    content: str
    observed_at: UtcInstant


class PresentationObservation(FrozenContract):
    operation_id: OperationId
    turn_ref: TurnRef
    response_id: ResponseId
    segment_id: Optional[SegmentId] = None
    presentation_reference: str
    observed_at: UtcInstant
