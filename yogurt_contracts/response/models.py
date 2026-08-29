from typing import Optional, Tuple

from pydantic import model_validator

from ..conversation.refs import TurnRef
from ..foundation.identifiers import ResponseId, SegmentId
from ..foundation.model import FrozenContract
from .lifecycle import ResponseState


class AgentOutputSegmentCommitted(FrozenContract):
    response_id: ResponseId
    segment_id: SegmentId
    sequence: int
    text: str
    language: str
    affect: Optional[str] = None
    interruptibility: Optional[str] = None
    content_hash: Optional[str] = None
    is_segment_final: bool = True

    @model_validator(mode="after")
    def has_valid_committed_content(self) -> "AgentOutputSegmentCommitted":
        if self.sequence < 1:
            raise ValueError("committed segment sequence must be at least 1")
        if not self.text:
            raise ValueError("committed segment text must be non-empty")
        if not self.language:
            raise ValueError("committed segment language must be non-empty")
        return self


class AgentResponseStarted(FrozenContract):
    response_id: ResponseId
    turn_ref: TurnRef


class AgentResponseFinalized(FrozenContract):
    response_id: ResponseId
    assembly_hash: Optional[str] = None


class AgentResponseAborted(FrozenContract):
    response_id: ResponseId
    # R1 public record remains wire-compatible. R2-A's authoritative response
    # event stream uses ResponseAbortReason rather than this legacy payload.
    reason: str


class LogicalResponse(FrozenContract):
    response_id: ResponseId
    state: ResponseState = ResponseState.STARTED
    segments: Tuple[AgentOutputSegmentCommitted, ...] = ()

    def append_segment(self, segment: AgentOutputSegmentCommitted) -> "LogicalResponse":
        if self.state != ResponseState.STARTED or segment.response_id != self.response_id:
            raise ValueError("cannot append segment to closed or different response")
        if self.segments and segment.sequence <= self.segments[-1].sequence:
            raise ValueError("segments must append in strictly increasing sequence")
        return self.model_copy(update={"segments": self.segments + (segment,)})
