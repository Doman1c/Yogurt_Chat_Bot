"""Crash-recoverable CORE_DOMAIN_INTERNAL ledger facts with inline immutable payloads."""

from typing import Optional, Union

from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.conversation.idempotency import TurnIdempotencyRecord
from yogurt_contracts.conversation.input import TurnInput
from yogurt_contracts.conversation.refs import TurnRef
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.identifiers import OperationId
from yogurt_contracts.foundation.model import FrozenContract
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_contracts.operations.outcome import OperationOutcome
from yogurt_contracts.presentation.facts import PresentationAccepted, PresentationDelivered, PresentationProgressObserved, PresentationTerminal
from yogurt_contracts.response.models import AgentOutputSegmentCommitted, AgentResponseAborted, AgentResponseFinalized, AgentResponseStarted


class _LedgerFact(FrozenContract):
    turn_ref: Optional[TurnRef] = None
    recorded_at: UtcInstant


class InputCommittedRecorded(_LedgerFact):
    turn_ref: TurnRef
    turn_input: TurnInput
    turn_fingerprint: TurnFingerprint
    idempotency_record: TurnIdempotencyRecord
    durability_level: DurabilityLevel


class ResponseStartedRecorded(_LedgerFact): response_started: AgentResponseStarted
class SegmentCommittedRecorded(_LedgerFact): segment: AgentOutputSegmentCommitted; durability_level: DurabilityLevel
class ResponseFinalizedRecorded(_LedgerFact): response_finalized: AgentResponseFinalized
class ResponseAbortedRecorded(_LedgerFact): response_aborted: AgentResponseAborted
class OperationIntentRecorded(_LedgerFact): intent: OperationIntentCommitted
class OperationOutcomeRecorded(_LedgerFact): operation_id: OperationId; outcome: OperationOutcome
PresentationFact = Union[PresentationAccepted, PresentationDelivered, PresentationProgressObserved, PresentationTerminal]
class PresentationFactRecorded(_LedgerFact): presentation_fact: PresentationFact
class TurnCompletedRecorded(_LedgerFact): turn_ref: TurnRef
class TurnAbortedRecorded(_LedgerFact): turn_ref: TurnRef; reason: str

InputCommitted = InputCommittedRecorded
