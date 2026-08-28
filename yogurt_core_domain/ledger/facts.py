"""Crash-recoverable CORE_DOMAIN_INTERNAL ledger facts with inline immutable payloads."""

from typing import Optional, Union

from pydantic import model_validator

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

    @model_validator(mode="after")
    def correlates_to_idempotency_record(self) -> "InputCommittedRecorded":
        key = self.idempotency_record.key
        if key.conversation_ref.conversation_id != self.turn_ref.conversation_id:
            raise ValueError("input commit conversation must match idempotency key")
        if key.turn_id != self.turn_ref.turn_id:
            raise ValueError("input commit turn must match idempotency key")
        if self.turn_fingerprint != self.idempotency_record.fingerprint:
            raise ValueError("input commit fingerprint must match idempotency record")
        return self


class ResponseStartedRecorded(_LedgerFact):
    response_started: AgentResponseStarted

    @model_validator(mode="after")
    def correlates_to_response(self) -> "ResponseStartedRecorded":
        if self.turn_ref != self.response_started.turn_ref:
            raise ValueError("response start outer turn must match response turn")
        return self


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
