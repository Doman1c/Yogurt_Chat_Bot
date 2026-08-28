from typing import Optional, Tuple

from ..foundation.identifiers import SegmentId
from ..foundation.model import FrozenContract


class TextPresentationExtent(FrozenContract):
    segment_id: SegmentId
    end_text_offset: int
    offset_unit: str = "unicode_codepoint"


class VoicePresentationExtent(FrozenContract):
    segment_id: SegmentId
    played_duration_ms: int
    end_text_offset: Optional[int] = None
    confidence: Optional[float] = None


class MessagingPresentationExtent(FrozenContract):
    fully_delivered_segment_ids: Tuple[SegmentId, ...]
    external_message_refs: Tuple[str, ...] = ()
