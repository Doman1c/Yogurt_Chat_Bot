from enum import Enum
from typing import Optional, Tuple

from pydantic import field_validator
from ..foundation.identifiers import SegmentId
from ..foundation.model import FrozenContract


class TextOffsetUnit(str, Enum):
    UNICODE_CODEPOINT = "unicode_codepoint"
    UTF8_BYTE = "utf8_byte"


class TextPresentationExtent(FrozenContract):
    segment_id: SegmentId
    end_text_offset: int
    offset_unit: TextOffsetUnit = TextOffsetUnit.UNICODE_CODEPOINT

    @field_validator("end_text_offset")
    @classmethod
    def has_nonnegative_offset(cls, value: int) -> int:
        if value < 0:
            raise ValueError("end_text_offset must be non-negative")
        return value


class VoicePresentationExtent(FrozenContract):
    """Voice text offsets, when available, are Unicode codepoint offsets."""
    segment_id: SegmentId
    played_duration_ms: int
    end_text_offset: Optional[int] = None
    confidence: Optional[float] = None

    @field_validator("played_duration_ms")
    @classmethod
    def has_nonnegative_duration(cls, value: int) -> int:
        if value < 0:
            raise ValueError("played_duration_ms must be non-negative")
        return value

    @field_validator("end_text_offset")
    @classmethod
    def has_nonnegative_end_offset(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("end_text_offset must be non-negative")
        return value

    @field_validator("confidence")
    @classmethod
    def has_bounded_confidence(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class MessagingPresentationExtent(FrozenContract):
    fully_delivered_segment_ids: Tuple[SegmentId, ...]
    external_message_refs: Tuple[str, ...] = ()

    @field_validator("fully_delivered_segment_ids", "external_message_refs")
    @classmethod
    def has_unique_values(cls, value: Tuple[object, ...]) -> Tuple[object, ...]:
        if len(value) != len(set(value)):
            raise ValueError("messaging presentation extent values must be unique")
        return value
