from typing import Iterable, List, Optional, Tuple

from ..foundation.identifiers import OperationId, ReceiptId
from ..foundation.model import FrozenContract
from .extent import MessagingPresentationExtent, TextPresentationExtent, VoicePresentationExtent
from .facts import PresentationAccepted, PresentationDelivered, PresentationProgressObserved, PresentationTerminal


class PresentationProjection(FrozenContract):
    operation_id: OperationId
    accepted_receipts: Tuple[ReceiptId, ...] = ()
    delivered_receipts: Tuple[ReceiptId, ...] = ()
    max_text_extent: Optional[TextPresentationExtent] = None
    max_voice_extent: Optional[VoicePresentationExtent] = None
    messaging_extent: Optional[MessagingPresentationExtent] = None
    terminal_outcome: Optional[str] = None


def project_presentation(operation_id: OperationId, facts: Iterable[object]) -> PresentationProjection:
    accepted: List[ReceiptId] = []
    delivered: List[ReceiptId] = []
    text_extent = voice_extent = messaging_extent = None
    terminal = None
    seen = set()
    for fact in facts:
        receipt_id = getattr(fact, "receipt_id", None)
        if not isinstance(receipt_id, ReceiptId) or getattr(fact, "operation_id", None) != operation_id or receipt_id in seen:
            continue
        seen.add(receipt_id)
        if isinstance(fact, PresentationAccepted): accepted.append(receipt_id)
        elif isinstance(fact, PresentationDelivered): delivered.append(receipt_id)
        elif isinstance(fact, PresentationProgressObserved):
            extent = fact.extent
            if isinstance(extent, TextPresentationExtent) and (text_extent is None or extent.end_text_offset > text_extent.end_text_offset): text_extent = extent
            elif isinstance(extent, VoicePresentationExtent) and (voice_extent is None or extent.played_duration_ms > voice_extent.played_duration_ms): voice_extent = extent
            elif isinstance(extent, MessagingPresentationExtent): messaging_extent = extent
        elif isinstance(fact, PresentationTerminal): terminal = fact.outcome
    return PresentationProjection(operation_id=operation_id, accepted_receipts=tuple(accepted), delivered_receipts=tuple(delivered), max_text_extent=text_extent, max_voice_extent=voice_extent, messaging_extent=messaging_extent, terminal_outcome=terminal)
