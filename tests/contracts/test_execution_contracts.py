from datetime import datetime, timezone

import pytest

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.lifecycle import TurnState, validate_turn_transition
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin, TurnRef
from yogurt_contracts.foundation.identifiers import BindingId, CapabilityId, ConversationId, ExternalPrincipalRef, IdentityResolutionRef, InputId, OperationId, PersonaId, ReceiptId, ResponseId, RuntimeInstanceId, RuntimeSlotId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.presentation.extent import VoicePresentationExtent
from yogurt_contracts.presentation.facts import PresentationProgressObserved, PresentationTerminal, PresentationTerminalOutcome
from yogurt_contracts.presentation.projection import project_presentation
from yogurt_contracts.response.models import AgentOutputSegmentCommitted, LogicalResponse
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
ORIGIN = TurnOrigin(origin_surface="native_voice", external_principal_ref=ExternalPrincipalRef(value="principal"), identity_resolution_ref=IdentityResolutionRef(value="resolution"))
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))


def test_fingerprint_v1_normalizes_unicode_and_line_endings_but_not_whitespace():
    first = TurnInput(input_id=InputId(value="input-1"), parts=(TextInputPart(text="e\u0301\r\nhello "),), origin=ORIGIN)
    equivalent = TurnInput(input_id=InputId(value="input-2"), parts=(TextInputPart(text="é\nhello "),), origin=ORIGIN)
    changed = TurnInput(input_id=InputId(value="input-3"), parts=(TextInputPart(text="é\nhello"),), origin=ORIGIN)
    assert turn_fingerprint(first, "binding") == turn_fingerprint(equivalent, "binding")
    assert turn_fingerprint(first, "binding") != turn_fingerprint(changed, "binding")


def test_response_segments_are_append_only_across_attempts():
    response = LogicalResponse(response_id=ResponseId(value="response"))
    segment = AgentOutputSegmentCommitted(response_id=ResponseId(value="response"), segment_id=SegmentId(value="segment-1"), sequence=1, text="hello", language="en")
    updated = response.append_segment(segment)
    assert response.segments == ()
    assert updated.segments == (segment,)
    with pytest.raises(ValueError):
        updated.append_segment(segment)


def test_turn_completed_does_not_allow_new_processing_transition():
    assert validate_turn_transition(TurnState.RECEIVED, TurnState.INPUT_COMMITTED)
    assert not validate_turn_transition(TurnState.COMPLETED, TurnState.PROCESSING)


def test_presentation_projection_dedupes_and_keeps_max_extent_with_failed_terminal():
    operation = OperationId(value="presentation-operation")
    high = PresentationProgressObserved(receipt_id=ReceiptId(value="high"), operation_id=operation, observed_at=NOW, extent=VoicePresentationExtent(segment_id=SegmentId(value="segment"), played_duration_ms=700))
    stale = PresentationProgressObserved(receipt_id=ReceiptId(value="stale"), operation_id=operation, observed_at=NOW, extent=VoicePresentationExtent(segment_id=SegmentId(value="segment"), played_duration_ms=400))
    projection = project_presentation(operation, (high, stale, high, PresentationTerminal(receipt_id=ReceiptId(value="failure"), operation_id=operation, observed_at=NOW, outcome=PresentationTerminalOutcome.FAILED)))
    assert projection.max_voice_extent.played_duration_ms == 700
    assert projection.terminal_outcome == "failed"


def test_operation_intent_is_explicit_write_ahead_contract():
    binding = CapabilityBinding(binding_id=BindingId(value="binding"), binding_revision="1", capability_id=CapabilityId(value="capability"), provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=RuntimeInstanceId(value="instance"), provider_generation=1, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=NOW)
    claims = AuthorityClaims(actor=RuntimeInstanceId(value="actor"), subject=UserId(value="user"), origin="surface", audience=RuntimeInstanceId(value="audience"), scope=CapabilityId(value="capability"), grant_revision="1", valid_until=NOW, authority_reference="authority")
    operation = LogicalOperation(operation_id=OperationId(value="operation"), request_fingerprint=RequestFingerprint(value="digest"), target_capability=CapabilityId(value="capability"), authority=DelegatedAuthority(claims=claims), deadline=Deadline(expires_at=NOW), traits=OperationTraits(effect_kind=EffectKind.WRITE, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.COMPENSATABLE, cancellation_support=CancellationSupport.BEST_EFFORT), created_at=NOW)
    from yogurt_contracts.foundation.durability import DurabilityLevel
    assert OperationIntentCommitted(operation=operation, provider_binding=binding, required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=NOW).operation.operation_id == OperationId(value="operation")
