from datetime import datetime, timezone

from yogurt_conformance.fixtures import PYTHON_BINDING_FIXTURE
from yogurt_contracts.foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from yogurt_contracts.foundation.identifiers import BindingId, ExecutionAttemptId, HypothesisId, LeaseId, OperationAttemptId, OperationId, PersonaId, ReceiptId, ResponseId, RuntimeInstanceId, RuntimeSlotId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import Deadline, Duration, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.presentation.extent import MessagingPresentationExtent, TextPresentationExtent, VoicePresentationExtent
from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.idempotency import TurnIdempotencyKey, TurnIdempotencyRecord
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin, TurnRef
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.identifiers import CapabilityId, ConversationId, ExternalPrincipalRef, InputId, IdentityResolutionRef
from yogurt_contracts.memory.observations import CommittedContentObservation, PresentationObservation, UserContentObservation
from yogurt_contracts.memory.projection import MemoryExposureProjection
from yogurt_contracts.presentation.facts import PresentationEvidenceLevel, PresentationProgressObserved
from yogurt_contracts.response.attempts import AttemptDisposition, ExecutionAttempt
from yogurt_contracts.response.models import AgentOutputSegmentCommitted, AgentResponseAborted, AgentResponseFinalized, AgentResponseStarted
from yogurt_contracts.capability.framework import BaseCapabilityDescriptor, CapabilityState
from yogurt_contracts.capability.offer import CapabilityOffer
from yogurt_contracts.operations.attempts import DispatchAttempt
from yogurt_contracts.runtime.lease import RuntimeLease
from yogurt_conformance.profiles.operation_provider import _typed_inputs


def test_python_binding_fixture_round_trip_is_not_wire_abi():
    model = DomainError(code=ErrorCode.INVALID_ARGUMENT, category=ErrorCategory.VALIDATION, retry=RetryDisposition.DO_NOT_RETRY, safe_message=PYTHON_BINDING_FIXTURE)
    assert DomainError.model_validate_json(model.model_dump_json()) == model
    instant = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert UtcInstant.model_validate_json(instant.model_dump_json()) == instant
    assert OperationId.model_validate_json(OperationId(value="op").model_dump_json()) == OperationId(value="op")


def test_python_binding_fixture_round_trips_r1_primitives_and_extents():
    models = (
        TurnId(value="turn"), ResponseId(value="response"), SegmentId(value="segment"), OperationId(value="operation"),
        ExecutionAttemptId(value="execution-attempt"), OperationAttemptId(value="operation-attempt"), RuntimeSlotId(value="slot"), RuntimeInstanceId(value="instance"),
        BindingId(value="binding"), LeaseId(value="lease"), UserId(value="user"), PersonaId(value="persona"), ReceiptId(value="receipt"), HypothesisId(value="hypothesis"),
        Duration(milliseconds=1), Deadline(expires_at=UtcInstant(value=datetime(2026, 1, 2, tzinfo=timezone.utc))), ContractVersion(major=1, minor=4),
        TextPresentationExtent(segment_id=SegmentId(value="segment"), end_text_offset=2), VoicePresentationExtent(segment_id=SegmentId(value="segment"), played_duration_ms=4, end_text_offset=2), MessagingPresentationExtent(fully_delivered_segment_ids=(SegmentId(value="segment"),), external_message_refs=("external",)),
    )
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model


def test_python_binding_fixture_round_trips_primary_complex_r1_contracts():
    now = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
    conversation = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))
    turn_ref = TurnRef(conversation_id=conversation.conversation_id, turn_id=TurnId(value="turn"))
    turn_input = TurnInput(input_id=InputId(value="input"), parts=(TextInputPart(text="hello"),), origin=TurnOrigin(origin_surface="test", external_principal_ref=ExternalPrincipalRef(value="principal"), identity_resolution_ref=IdentityResolutionRef(value="resolution")))
    fingerprint = turn_fingerprint(turn_input, "binding")
    response_id = ResponseId(value="response")
    segment = AgentOutputSegmentCommitted(response_id=response_id, segment_id=SegmentId(value="segment"), sequence=1, text="hello", language="en")
    intent, authorization, runtime, assignment = _typed_inputs()
    committed = CommittedContentObservation(operation_id=OperationId(value="memory-commit"), conversation_ref=conversation, turn_ref=turn_ref, response_id=response_id, segment_id=segment.segment_id, content="hello", observed_at=now)
    presentation = PresentationObservation(operation_id=OperationId(value="memory-presentation"), conversation_ref=conversation, turn_ref=turn_ref, response_id=response_id, presentation_operation_id=OperationId(value="present"), receipt_id=ReceiptId(value="receipt"), evidence_level=PresentationEvidenceLevel.PRESENTED_TO_USER, segment_id=segment.segment_id, extent=TextPresentationExtent(segment_id=segment.segment_id, end_text_offset=5), observed_at=now)
    models = (
        turn_input, fingerprint, TurnIdempotencyRecord(key=TurnIdempotencyKey(conversation_ref=conversation, turn_id=turn_ref.turn_id), fingerprint=fingerprint, admission_result="accepted", committed_at=now, required_durability=DurabilityLevel.DURABLY_PERSISTED),
        AgentResponseStarted(response_id=response_id, turn_ref=turn_ref), segment, AgentResponseFinalized(response_id=response_id), AgentResponseAborted(response_id=response_id, reason="test"),
        ExecutionAttempt(execution_attempt_id=ExecutionAttemptId(value="attempt"), response_id=response_id, attempt_number=1, provider_binding_id=intent.provider_binding.binding_id, started_at=now, ended_at=now, disposition=AttemptDisposition.SUCCEEDED),
        intent.operation, intent, authorization, intent.provider_binding, DispatchAttempt(operation_attempt_id=OperationAttemptId(value="dispatch"), operation_id=intent.operation.operation_id, attempt_number=1, provider_binding_id=intent.provider_binding.binding_id, dispatch_started_at=now, dispatch_finished_at=now),
        CapabilityOffer[BaseCapabilityDescriptor](capability_id=CapabilityId(value="offer"), contract_version=ContractVersion(major=1, minor=0), provider_slot_id=runtime.runtime_slot_id, provider_instance_id=runtime.runtime_instance_id, provider_generation=1, descriptor=BaseCapabilityDescriptor(), state=CapabilityState.AVAILABLE, lease_expires_at=now), runtime, RuntimeLease(lease_id=LeaseId(value="lease"), runtime=runtime, issued_at=now, expires_at=UtcInstant(value=datetime(2026, 1, 2, tzinfo=timezone.utc)), issuer="issuer"), assignment,
        UserContentObservation(operation_id=OperationId(value="user-observation"), conversation_ref=conversation, turn_ref=turn_ref, input_id=InputId(value="input"), content="hello", observed_at=now), committed, presentation,
        PresentationProgressObserved(receipt_id=ReceiptId(value="presentation-receipt"), operation_id=OperationId(value="present"), observed_at=now, extent=TextPresentationExtent(segment_id=segment.segment_id, end_text_offset=5)),
        MemoryExposureProjection(committed_content=True, text_extent=TextPresentationExtent(segment_id=segment.segment_id, end_text_offset=5), is_fully_exposed=True),
    )
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model
