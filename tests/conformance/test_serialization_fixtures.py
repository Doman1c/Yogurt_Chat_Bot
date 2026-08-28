from datetime import datetime, timezone

from yogurt_conformance.fixtures import PYTHON_BINDING_FIXTURE
from yogurt_contracts.foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from yogurt_contracts.foundation.identifiers import BindingId, ExecutionAttemptId, HypothesisId, LeaseId, OperationAttemptId, OperationId, PersonaId, ReceiptId, ResponseId, RuntimeInstanceId, RuntimeSlotId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import Deadline, Duration, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.presentation.extent import MessagingPresentationExtent, TextPresentationExtent, VoicePresentationExtent


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
