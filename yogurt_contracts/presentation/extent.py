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


def normalize_text_extent_progress(content: str, extent: TextPresentationExtent) -> int:
    """Return covered Unicode codepoints without guessing across offset units.

    UTF-8 offsets that end within a multibyte codepoint cover only the complete
    codepoints preceding that partial byte sequence.
    """

    if extent.offset_unit == TextOffsetUnit.UNICODE_CODEPOINT:
        return min(extent.end_text_offset, len(content))
    consumed = 0
    covered = 0
    for character in content:
        width = len(character.encode("utf-8"))
        if consumed + width > extent.end_text_offset:
            break
        consumed += width
        covered += 1
    return covered


def merge_text_extent(
    content: str,
    current: Optional[TextPresentationExtent],
    candidate: TextPresentationExtent,
) -> TextPresentationExtent:
    """Keep semantic progress with a deterministic canonical tie-breaker."""

    if current is None:
        return candidate
    candidate_progress = normalize_text_extent_progress(content, candidate)
    current_progress = normalize_text_extent_progress(content, current)
    if candidate_progress > current_progress:
        return candidate
    if candidate_progress < current_progress:
        return current
    # Equal semantic progress must not make a projection order-dependent.  The
    # Unicode representation is canonical, then the smaller raw offset.
    def canonical_key(extent: TextPresentationExtent) -> tuple[int, int, str]:
        return (
            0 if extent.offset_unit == TextOffsetUnit.UNICODE_CODEPOINT else 1,
            extent.end_text_offset,
            extent.segment_id.value,
        )
    return min(current, candidate, key=canonical_key)


def merge_voice_extent(
    current: Optional[VoicePresentationExtent],
    candidate: VoicePresentationExtent,
) -> VoicePresentationExtent:
    """Merge voice progress component-wise; confidence follows text progress."""

    if current is None:
        return candidate
    current_offset = current.end_text_offset
    merged_offset: Optional[int]
    candidate_offset = candidate.end_text_offset
    if candidate_offset is not None and (current_offset is None or candidate_offset > current_offset):
        merged_offset = candidate_offset
        confidence = candidate.confidence
    else:
        merged_offset = current_offset
        confidence = current.confidence
    return VoicePresentationExtent(
        segment_id=current.segment_id,
        played_duration_ms=max(current.played_duration_ms, candidate.played_duration_ms),
        end_text_offset=merged_offset,
        confidence=confidence,
    )
