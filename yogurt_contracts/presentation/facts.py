from enum import Enum
from typing import Optional, Union

from ..foundation.identifiers import OperationId, ReceiptId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from .extent import MessagingPresentationExtent, TextPresentationExtent, VoicePresentationExtent


class _PresentationFact(FrozenContract):
    receipt_id: ReceiptId
    operation_id: OperationId
    observed_at: UtcInstant
    provider_evidence_reference: Optional[str] = None


class PresentationAccepted(_PresentationFact): pass
class PresentationDelivered(_PresentationFact): pass

class PresentationEvidenceLevel(str, Enum):
    """Strictly ordered surface evidence: acceptance, delivery, user-facing presentation, then explicit read."""

    PROVIDER_ACCEPTED = "provider_accepted"
    DELIVERED = "delivered"
    PRESENTED_TO_USER = "presented_to_user"
    READ_BY_USER = "read_by_user"
class PresentationTerminalOutcome(str, Enum): COMPLETED = "completed"; INTERRUPTED = "interrupted"; FAILED = "failed"


class PresentationProgressObserved(_PresentationFact):
    extent: Union[TextPresentationExtent, VoicePresentationExtent, MessagingPresentationExtent]


class PresentationTerminal(_PresentationFact):
    outcome: PresentationTerminalOutcome


def presentation_evidence_rank(level: PresentationEvidenceLevel) -> int:
    """Monotonic evidence ordering; callers must never infer stronger evidence."""

    return {
        PresentationEvidenceLevel.PROVIDER_ACCEPTED: 0,
        PresentationEvidenceLevel.DELIVERED: 1,
        PresentationEvidenceLevel.PRESENTED_TO_USER: 2,
        PresentationEvidenceLevel.READ_BY_USER: 3,
    }[level]
