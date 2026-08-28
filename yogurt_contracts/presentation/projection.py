from typing import Iterable, List, Mapping, Optional, Tuple

from ..foundation.identifiers import OperationId, ReceiptId
from ..foundation.model import FrozenContract
from .extent import MessagingPresentationExtent, TextOffsetUnit, TextPresentationExtent, VoicePresentationExtent, merge_text_extent, merge_voice_extent
from .facts import PresentationAccepted, PresentationDelivered, PresentationProgressObserved, PresentationTerminal, PresentationTerminalOutcome


class PresentationProjection(FrozenContract):
    operation_id: OperationId
    accepted_receipts: Tuple[ReceiptId, ...] = ()
    delivered_receipts: Tuple[ReceiptId, ...] = ()
    max_text_extent: Optional[TextPresentationExtent] = None
    max_voice_extent: Optional[VoicePresentationExtent] = None
    text_extents: Tuple[TextPresentationExtent, ...] = ()
    voice_extents: Tuple[VoicePresentationExtent, ...] = ()
    messaging_extent: Optional[MessagingPresentationExtent] = None
    terminal_outcome: Optional[PresentationTerminalOutcome] = None


def project_presentation(
    operation_id: OperationId,
    facts: Iterable[object],
    segment_contents: Optional[Mapping[object, str]] = None,
) -> PresentationProjection:
    accepted: List[ReceiptId] = []
    delivered: List[ReceiptId] = []
    text_extents: dict[object, TextPresentationExtent] = {}
    voice_extents: dict[object, VoicePresentationExtent] = {}
    messaging_extent = None
    terminal = None
    seen: dict[ReceiptId, object] = {}
    for fact in facts:
        receipt_id = getattr(fact, "receipt_id", None)
        if not isinstance(receipt_id, ReceiptId) or getattr(fact, "operation_id", None) != operation_id:
            continue
        previous = seen.get(receipt_id)
        if previous is not None:
            if previous != fact:
                raise ValueError("conflicting presentation receipt facts")
            continue
        seen[receipt_id] = fact
        if isinstance(fact, PresentationAccepted): accepted.append(receipt_id)
        elif isinstance(fact, PresentationDelivered): delivered.append(receipt_id)
        elif isinstance(fact, PresentationProgressObserved):
            extent = fact.extent
            if isinstance(extent, TextPresentationExtent):
                content = (segment_contents or {}).get(extent.segment_id)
                current = text_extents.get(extent.segment_id)
                if content is not None:
                    text_extents[extent.segment_id] = merge_text_extent(content, current, extent)
                elif current is None or extent.offset_unit == current.offset_unit and extent.end_text_offset > current.end_text_offset:
                    text_extents[extent.segment_id] = extent
                elif current.offset_unit == TextOffsetUnit.UTF8_BYTE and extent.offset_unit == TextOffsetUnit.UNICODE_CODEPOINT:
                    # Without associated immutable segment content the units are
                    # incomparable.  This deterministic canonicalization avoids
                    # order-sensitive raw integer comparisons; callers that need
                    # semantic progress must supply segment_contents.
                    text_extents[extent.segment_id] = extent
            elif isinstance(extent, VoicePresentationExtent):
                voice_extents[extent.segment_id] = merge_voice_extent(voice_extents.get(extent.segment_id), extent)
            elif isinstance(extent, MessagingPresentationExtent):
                if messaging_extent is None:
                    messaging_extent = extent
                else:
                    messaging_extent = MessagingPresentationExtent(fully_delivered_segment_ids=tuple(dict.fromkeys(messaging_extent.fully_delivered_segment_ids + extent.fully_delivered_segment_ids)), external_message_refs=tuple(dict.fromkeys(messaging_extent.external_message_refs + extent.external_message_refs)))
        elif isinstance(fact, PresentationTerminal):
            if terminal is not None and terminal != fact.outcome:
                raise ValueError("conflicting presentation terminal facts")
            terminal = fact.outcome
    ordered_text = tuple(sorted(text_extents.values(), key=lambda extent: extent.segment_id.value))
    ordered_voice = tuple(sorted(voice_extents.values(), key=lambda extent: extent.segment_id.value))
    return PresentationProjection(
        operation_id=operation_id,
        accepted_receipts=tuple(accepted),
        delivered_receipts=tuple(delivered),
        # Legacy single-segment convenience fields deliberately do not choose an
        # arbitrary cross-segment maximum.
        max_text_extent=ordered_text[0] if len(ordered_text) == 1 else None,
        max_voice_extent=ordered_voice[0] if len(ordered_voice) == 1 else None,
        text_extents=ordered_text,
        voice_extents=ordered_voice,
        messaging_extent=messaging_extent,
        terminal_outcome=terminal,
    )
