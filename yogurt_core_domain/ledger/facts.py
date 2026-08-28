"""Append-only CORE_DOMAIN_INTERNAL ledger facts, not a cross-runtime API."""

from typing import Optional

from yogurt_contracts.conversation.refs import TurnRef
from yogurt_contracts.foundation.identifiers import OperationId, ResponseId, SegmentId
from yogurt_contracts.foundation.model import FrozenContract
from yogurt_contracts.foundation.time import UtcInstant


class _LedgerFact(FrozenContract):
    turn_ref: TurnRef
    recorded_at: UtcInstant


class InputCommitted(_LedgerFact): pass
class ResponseStartedRecorded(_LedgerFact): response_id: ResponseId
class SegmentCommittedRecorded(_LedgerFact): response_id: ResponseId; segment_id: SegmentId
class ResponseFinalizedRecorded(_LedgerFact): response_id: ResponseId
class ResponseAbortedRecorded(_LedgerFact): response_id: ResponseId; reason: str
class OperationIntentRecorded(_LedgerFact): operation_id: OperationId
class OperationOutcomeRecorded(_LedgerFact): operation_id: OperationId; outcome: str
class PresentationFactRecorded(_LedgerFact): operation_id: OperationId; receipt_reference: str
class TurnCompletedRecorded(_LedgerFact): pass
class TurnAbortedRecorded(_LedgerFact): reason: str
