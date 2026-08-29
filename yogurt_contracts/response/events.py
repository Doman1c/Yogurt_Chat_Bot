"""Narrow replayable event contract for one logical response."""

from typing import Literal, Union

from ..foundation.identifiers import ResponseEventId, ResponseId, SegmentId
from ..foundation.model import FrozenContract


class ResponseEventBase(FrozenContract):
    event_id: ResponseEventId
    response_id: ResponseId
    event_sequence: int


class ResponseStartedEvent(ResponseEventBase):
    event_type: Literal["response_started"] = "response_started"


class SegmentCommittedEvent(ResponseEventBase):
    event_type: Literal["segment_committed"] = "segment_committed"
    segment_id: SegmentId
    segment_sequence: int
    text: str
    language: str


class ResponseFinalizedEvent(ResponseEventBase):
    event_type: Literal["response_finalized"] = "response_finalized"


class ResponseAbortedEvent(ResponseEventBase):
    event_type: Literal["response_aborted"] = "response_aborted"
    reason: str


ResponseEvent = Union[ResponseStartedEvent, SegmentCommittedEvent, ResponseFinalizedEvent, ResponseAbortedEvent]
