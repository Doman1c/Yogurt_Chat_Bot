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


class PresentationProgressObserved(_PresentationFact):
    extent: Union[TextPresentationExtent, VoicePresentationExtent, MessagingPresentationExtent]


class PresentationTerminal(_PresentationFact):
    outcome: str
