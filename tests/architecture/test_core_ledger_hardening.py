"""Regression coverage for R1 authoritative ledger identity and ordering rules."""

from datetime import datetime, timezone
from typing import Optional

import pytest

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.conversation.idempotency import TurnIdempotencyKey, TurnIdempotencyRecord
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin, TurnRef
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.identifiers import (
    BindingId,
    CapabilityId,
    ConversationId,
    ExternalPrincipalRef,
    IdentityResolutionRef,
    InputId,
    OperationId,
    PersonaId,
    ReceiptId,
    ResponseId,
    RuntimeInstanceId,
    RuntimeSlotId,
    SegmentId,
    TurnId,
    UserId,
)
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.outcome import OperationOutcome
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_contracts.presentation.facts import PresentationTerminal, PresentationTerminalOutcome
from yogurt_contracts.response.models import AgentOutputSegmentCommitted, AgentResponseAborted, AgentResponseFinalized, AgentResponseStarted
from yogurt_conformance.assertions.turns import assert_turn_retry
from yogurt_contracts.conversation.idempotency import TurnRetryDecision
from yogurt_core_domain.ledger.facts import (
    InputCommittedRecorded,
    OperationIntentRecorded,
    OperationOutcomeRecorded,
    PresentationFactRecorded,
    ResponseAbortedRecorded,
    ResponseFinalizedRecorded,
    ResponseStartedRecorded,
    SegmentCommittedRecorded,
    TurnAbortedRecorded,
    TurnCompletedRecorded,
)
from yogurt_core_domain.ledger.ordering import LedgerInvariantError, append_fact


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
CONVERSATION = ConversationRef(
    persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation")
)
ORIGIN = TurnOrigin(
    origin_surface="native_voice",
    external_principal_ref=ExternalPrincipalRef(value="principal"),
    identity_resolution_ref=IdentityResolutionRef(value="resolution"),
)


def _turn(name: str = "turn", sequence: Optional[int] = 1, parent: Optional[TurnId] = None) -> TurnRef:
    return TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value=name), sequence=sequence, parent_turn_id=parent)


def _fingerprint(value: str = "fingerprint") -> TurnFingerprint:
    digest = RequestFingerprint(value=value)
    return TurnFingerprint(input_digest=digest, origin_digest=digest, principal_binding_digest=digest)


def _input(turn_ref: TurnRef, fingerprint: Optional[TurnFingerprint] = None, key_turn_id: Optional[TurnId] = None) -> InputCommittedRecorded:
    fingerprint = fingerprint or _fingerprint()
    key = TurnIdempotencyKey(conversation_ref=CONVERSATION, turn_id=key_turn_id or turn_ref.turn_id)
    record = TurnIdempotencyRecord(
        key=key,
        fingerprint=fingerprint,
        admission_result="admitted",
        committed_at=NOW,
        required_durability=DurabilityLevel.DURABLY_PERSISTED,
    )
    return InputCommittedRecorded(
        turn_ref=turn_ref,
        recorded_at=NOW,
        turn_input=TurnInput(input_id=InputId(value="input-" + str(turn_ref.turn_id)), parts=(TextInputPart(text="hello"),), origin=ORIGIN),
        turn_fingerprint=fingerprint,
        idempotency_record=record,
        durability_level=DurabilityLevel.DURABLY_PERSISTED,
    )


def _start(turn_ref: TurnRef, response: str = "response") -> ResponseStartedRecorded:
    return ResponseStartedRecorded(
        turn_ref=turn_ref,
        recorded_at=NOW,
        response_started=AgentResponseStarted(response_id=ResponseId(value=response), turn_ref=turn_ref),
    )


def _segment(turn_ref: TurnRef, response: str = "response", sequence: int = 1) -> SegmentCommittedRecorded:
    return SegmentCommittedRecorded(
        turn_ref=turn_ref,
        recorded_at=NOW,
        durability_level=DurabilityLevel.JOURNALED,
        segment=AgentOutputSegmentCommitted(
            response_id=ResponseId(value=response), segment_id=SegmentId(value="segment-" + str(sequence)), sequence=sequence,
            text="hello", language="en", affect="warm", interruptibility="interruptible",
        ),
    )


def _finalized(turn_ref: TurnRef, response: str = "response") -> ResponseFinalizedRecorded:
    return ResponseFinalizedRecorded(
        turn_ref=turn_ref, recorded_at=NOW, response_finalized=AgentResponseFinalized(response_id=ResponseId(value=response), assembly_hash="hash")
    )


def _aborted(turn_ref: TurnRef, response: str = "response") -> ResponseAbortedRecorded:
    return ResponseAbortedRecorded(
        turn_ref=turn_ref, recorded_at=NOW, response_aborted=AgentResponseAborted(response_id=ResponseId(value=response), reason="cancelled")
    )


def _complete(turn_ref: TurnRef) -> TurnCompletedRecorded:
    return TurnCompletedRecorded(turn_ref=turn_ref, recorded_at=NOW)


def test_input_commit_round_trip_preserves_turn_input() -> None:
    fact = _input(_turn())
    restored = InputCommittedRecorded.model_validate(fact.model_dump())
    assert restored == fact
    assert restored.turn_input.parts == (TextInputPart(text="hello"),)


def test_segment_round_trip_preserves_recovery_payload() -> None:
    fact = _segment(_turn(), sequence=7)
    restored = SegmentCommittedRecorded.model_validate(fact.model_dump())
    assert restored.segment.response_id == ResponseId(value="response")
    assert restored.segment.segment_id == SegmentId(value="segment-7")
    assert restored.segment.sequence == 7
    assert restored.segment.text == "hello"
    assert restored.segment.language == "en"
    assert restored.segment.affect == "warm"
    assert restored.segment.interruptibility == "interruptible"


def test_operation_intent_round_trip_preserves_write_ahead_contract() -> None:
    binding = CapabilityBinding(
        binding_id=BindingId(value="binding"), binding_revision="1", capability_id=CapabilityId(value="capability"),
        provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=RuntimeInstanceId(value="instance"),
        provider_generation=1, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=NOW,
    )
    claims = AuthorityClaims(
        actor=RuntimeInstanceId(value="actor"), subject=UserId(value="user"), origin="surface", audience=RuntimeInstanceId(value="audience"),
        scope=CapabilityId(value="capability"), grant_revision="1", valid_until=NOW, authority_reference="authority",
    )
    operation = LogicalOperation(
        operation_id=OperationId(value="operation"), request_fingerprint=RequestFingerprint(value="request"), target_capability=CapabilityId(value="capability"),
        authority=DelegatedAuthority(claims=claims), deadline=Deadline(expires_at=NOW),
        traits=OperationTraits(effect_kind=EffectKind.WRITE, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.COMPENSATABLE, cancellation_support=CancellationSupport.BEST_EFFORT),
        created_at=NOW,
    )
    fact = OperationIntentRecorded(
        turn_ref=_turn(), recorded_at=NOW,
        intent=OperationIntentCommitted(operation=operation, provider_binding=binding, required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=NOW),
    )
    restored = OperationIntentRecorded.model_validate(fact.model_dump())
    assert restored.intent.operation.operation_id == OperationId(value="operation")
    assert restored.intent.operation.request_fingerprint == RequestFingerprint(value="request")
    assert restored.intent.operation.target_capability == CapabilityId(value="capability")
    assert restored.intent.provider_binding == binding
    assert restored.intent.required_durability == DurabilityLevel.DURABLY_PERSISTED


def test_recorded_payloads_reject_cross_turn_or_fingerprint_mismatches() -> None:
    turn_a = _turn("a")
    turn_b = _turn("b")
    with pytest.raises(ValueError):
        ResponseStartedRecorded(turn_ref=turn_a, recorded_at=NOW, response_started=AgentResponseStarted(response_id=ResponseId(value="response"), turn_ref=turn_b))
    with pytest.raises(ValueError):
        _input(turn_a, key_turn_id=turn_b.turn_id)
    fingerprint_a = _fingerprint("a")
    with pytest.raises(ValueError):
        InputCommittedRecorded(
            turn_ref=turn_a, recorded_at=NOW,
            turn_input=TurnInput(input_id=InputId(value="input-a"), parts=(TextInputPart(text="hello"),), origin=ORIGIN),
            turn_fingerprint=fingerprint_a,
            idempotency_record=TurnIdempotencyRecord(
                key=TurnIdempotencyKey(conversation_ref=CONVERSATION, turn_id=turn_a.turn_id), fingerprint=_fingerprint("b"),
                admission_result="admitted", committed_at=NOW, required_durability=DurabilityLevel.DURABLY_PERSISTED,
            ),
            durability_level=DurabilityLevel.DURABLY_PERSISTED,
        )


def test_turn_idempotency_uses_only_conversation_ref_and_turn_id() -> None:
    first_ref = _turn("turn", sequence=10)
    second_ref = _turn("turn", sequence=999, parent=TurnId(value="parent"))
    first_key = TurnIdempotencyKey(conversation_ref=CONVERSATION, turn_id=first_ref.turn_id)
    second_key = TurnIdempotencyKey(conversation_ref=CONVERSATION, turn_id=second_ref.turn_id)
    assert first_key == second_key
    assert assert_turn_retry(first_key, _fingerprint("same"), second_key, _fingerprint("same")) == TurnRetryDecision.SAME_LOGICAL_TURN
    assert assert_turn_retry(first_key, _fingerprint("first"), second_key, _fingerprint("changed")) == TurnRetryDecision.TURN_ID_CONFLICT
    other_turn_key = TurnIdempotencyKey(conversation_ref=CONVERSATION, turn_id=TurnId(value="other-turn"))
    assert assert_turn_retry(first_key, _fingerprint("same"), other_turn_key, _fingerprint("same")) == TurnRetryDecision.DIFFERENT_TURN
    other_conversation = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="other-conversation"))
    other_conversation_key = TurnIdempotencyKey(conversation_ref=other_conversation, turn_id=first_ref.turn_id)
    assert assert_turn_retry(first_key, _fingerprint("same"), other_conversation_key, _fingerprint("same")) == TurnRetryDecision.DIFFERENT_TURN


def test_ledger_accepts_completed_response_and_no_response_turns() -> None:
    turn = _turn()
    facts = append_fact((), _input(turn))
    facts = append_fact(facts, _start(turn))
    facts = append_fact(facts, _segment(turn))
    facts = append_fact(facts, _finalized(turn))
    assert append_fact(facts, _complete(turn))[-1] == _complete(turn)
    no_response_facts = append_fact((), _input(_turn("no-response")))
    assert isinstance(append_fact(no_response_facts, _complete(_turn("no-response")))[-1], TurnCompletedRecorded)


def test_ledger_rejects_response_facts_without_required_predecessor() -> None:
    turn = _turn()
    with pytest.raises(LedgerInvariantError):
        append_fact((), _start(turn))
    input_facts = append_fact((), _input(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(input_facts, _segment(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(input_facts, _finalized(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(input_facts, _aborted(turn))


def test_ledger_rejects_duplicate_and_conflicting_authoritative_facts() -> None:
    turn = _turn()
    input_fact = _input(turn)
    facts = append_fact((), input_fact)
    with pytest.raises(LedgerInvariantError):
        append_fact(facts, input_fact)
    started = _start(turn)
    facts = append_fact(facts, started)
    with pytest.raises(LedgerInvariantError):
        append_fact(facts, _start(turn, "response-2"))
    finalized = append_fact(facts, _finalized(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(finalized, _segment(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(finalized, _aborted(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(finalized, _finalized(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(facts, _finalized(turn, "other-response"))


def test_ledger_rejects_segments_after_aborted_response_and_turn_completion_before_safe() -> None:
    turn = _turn()
    started = append_fact(append_fact((), _input(turn)), _start(turn))
    aborted = append_fact(started, _aborted(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(aborted, _segment(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact((), _complete(turn))
    with pytest.raises(LedgerInvariantError):
        append_fact(started, _complete(turn))


def test_turn_completion_allows_late_downstream_facts_but_no_new_primary_facts() -> None:
    turn = _turn()
    facts = append_fact(append_fact(append_fact(append_fact((), _input(turn)), _start(turn)), _segment(turn)), _finalized(turn))
    completed = append_fact(facts, _complete(turn))
    presentation = PresentationFactRecorded(
        turn_ref=turn, recorded_at=NOW,
        presentation_fact=PresentationTerminal(receipt_id=ReceiptId(value="receipt"), operation_id=OperationId(value="present"), observed_at=NOW, outcome=PresentationTerminalOutcome.COMPLETED),
    )
    completed = append_fact(completed, presentation)
    completed = append_fact(completed, OperationOutcomeRecorded(turn_ref=turn, recorded_at=NOW, operation_id=OperationId(value="operation"), outcome=OperationOutcome.SUCCEEDED))
    assert isinstance(completed[-1], OperationOutcomeRecorded)
    with pytest.raises(LedgerInvariantError):
        append_fact(completed, _segment(turn, sequence=2))
    with pytest.raises(LedgerInvariantError):
        append_fact(completed, _start(turn, "response-2"))
    with pytest.raises(LedgerInvariantError):
        append_fact(completed, _input(turn))


def test_turn_aborted_and_completed_are_mutually_exclusive() -> None:
    turn = _turn()
    aborted = append_fact(append_fact((), _input(turn)), TurnAbortedRecorded(turn_ref=turn, recorded_at=NOW, reason="cancelled"))
    with pytest.raises(LedgerInvariantError):
        append_fact(aborted, _complete(turn))
