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
    messaging_extent: Optional[MessagingPresentationExtent] = None
    terminal_outcome: Optional[PresentationTerminalOutcome] = None


def project_presentation(
    operation_id: OperationId,
    facts: Iterable[object],
    segment_contents: Optional[Mapping[object, str]] = None,
) -> PresentationProjection:
    accepted: List[ReceiptId] = []
    delivered: List[ReceiptId] = []
    text_extent = voice_extent = messaging_extent = None
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
                if content is not None:
                    text_extent = merge_text_extent(content, text_extent, extent)
                elif text_extent is None or extent.offset_unit == text_extent.offset_unit and extent.end_text_offset > text_extent.end_text_offset:
                    text_extent = extent
                elif text_extent.offset_unit == TextOffsetUnit.UTF8_BYTE and extent.offset_unit == TextOffsetUnit.UNICODE_CODEPOINT:
                    # Without associated immutable segment content the units are
                    # incomparable.  This deterministic canonicalization avoids
                    # order-sensitive raw integer comparisons; callers that need
                    # semantic progress must supply segment_contents.
                    text_extent = extent
            elif isinstance(extent, VoicePresentationExtent):
                voice_extent = merge_voice_extent(voice_extent, extent)
            elif isinstance(extent, MessagingPresentationExtent):
                if messaging_extent is None:
                    messaging_extent = extent
                else:
                    messaging_extent = MessagingPresentationExtent(fully_delivered_segment_ids=tuple(dict.fromkeys(messaging_extent.fully_delivered_segment_ids + extent.fully_delivered_segment_ids)), external_message_refs=tuple(dict.fromkeys(messaging_extent.external_message_refs + extent.external_message_refs)))
        elif isinstance(fact, PresentationTerminal):
            if terminal is not None and terminal != fact.outcome:
                raise ValueError("conflicting presentation terminal facts")
            terminal = fact.outcome
    return PresentationProjection(operation_id=operation_id, accepted_receipts=tuple(accepted), delivered_receipts=tuple(delivered), max_text_extent=text_extent, max_voice_extent=voice_extent, messaging_extent=messaging_extent, terminal_outcome=terminal)
