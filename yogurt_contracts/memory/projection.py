"""Pure exposure projection over immutable public Memory observations."""

from typing import Iterable, Optional, Tuple

from ..foundation.identifiers import SegmentId
from ..foundation.model import FrozenContract
from ..presentation.extent import (
    MessagingPresentationExtent,
    TextPresentationExtent,
    VoicePresentationExtent,
    merge_text_extent,
    merge_voice_extent,
    normalize_text_extent_progress,
)
from ..presentation.facts import PresentationEvidenceLevel, presentation_evidence_rank
from .observations import CommittedContentObservation, PresentationObservation


class MemoryExposureProjection(FrozenContract):
    committed_content: bool
    highest_evidence: Optional[PresentationEvidenceLevel] = None
    text_extent: Optional[TextPresentationExtent] = None
    voice_extent: Optional[VoicePresentationExtent] = None
    messaging_extent: Optional[MessagingPresentationExtent] = None
    is_partially_exposed: bool = False
    is_fully_exposed: bool = False


def _max_messaging_extent(current: Optional[MessagingPresentationExtent], candidate: MessagingPresentationExtent) -> MessagingPresentationExtent:
    if current is None:
        return candidate
    segment_ids = tuple(dict.fromkeys(current.fully_delivered_segment_ids + candidate.fully_delivered_segment_ids))
    message_refs = tuple(dict.fromkeys(current.external_message_refs + candidate.external_message_refs))
    return MessagingPresentationExtent(fully_delivered_segment_ids=segment_ids, external_message_refs=message_refs)


def project_memory_exposure(
    committed_content: CommittedContentObservation,
    presentation_observations: Iterable[PresentationObservation],
) -> MemoryExposureProjection:
    """Project monotonic exposure evidence for one committed response/optional segment."""

    highest: Optional[PresentationEvidenceLevel] = None
    text_extent: Optional[TextPresentationExtent] = None
    voice_extent: Optional[VoicePresentationExtent] = None
    messaging_extent: Optional[MessagingPresentationExtent] = None
    for observation in presentation_observations:
        if observation.conversation_ref != committed_content.conversation_ref:
            continue
        if observation.turn_ref.turn_id != committed_content.turn_ref.turn_id:
            continue
        if observation.response_id != committed_content.response_id:
            continue
        if observation.segment_id != committed_content.segment_id:
            continue
        if highest is None or presentation_evidence_rank(observation.evidence_level) > presentation_evidence_rank(highest):
            highest = observation.evidence_level
        extent = observation.extent
        if isinstance(extent, TextPresentationExtent):
            text_extent = merge_text_extent(committed_content.content, text_extent, extent)
        elif isinstance(extent, VoicePresentationExtent):
            voice_extent = merge_voice_extent(voice_extent, extent)
        elif isinstance(extent, MessagingPresentationExtent):
            messaging_extent = _max_messaging_extent(messaging_extent, extent)
    presented_or_read = highest is not None and presentation_evidence_rank(highest) >= presentation_evidence_rank(PresentationEvidenceLevel.PRESENTED_TO_USER)
    full_text = (
        presented_or_read
        and text_extent is not None
        and normalize_text_extent_progress(committed_content.content, text_extent) == len(committed_content.content)
    )
    full_voice = presented_or_read and voice_extent is not None and voice_extent.end_text_offset is not None and voice_extent.end_text_offset >= len(committed_content.content)
    fully_exposed = full_text or full_voice
    positive_text = text_extent is not None and normalize_text_extent_progress(committed_content.content, text_extent) > 0
    positive_voice = voice_extent is not None and (voice_extent.played_duration_ms > 0 or (voice_extent.end_text_offset is not None and voice_extent.end_text_offset > 0))
    partially_exposed = presented_or_read and (positive_text or positive_voice) and not fully_exposed
    return MemoryExposureProjection(
        committed_content=True,
        highest_evidence=highest,
        text_extent=text_extent,
        voice_extent=voice_extent,
        messaging_extent=messaging_extent,
        is_partially_exposed=partially_exposed,
        is_fully_exposed=fully_exposed,
    )
