from datetime import datetime, timezone

from yogurt_conformance.fixtures import PYTHON_BINDING_FIXTURE
from yogurt_contracts.foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from yogurt_contracts.foundation.identifiers import OperationId
from yogurt_contracts.foundation.time import UtcInstant


def test_python_binding_fixture_round_trip_is_not_wire_abi():
    model = DomainError(code=ErrorCode.INVALID_ARGUMENT, category=ErrorCategory.VALIDATION, retry=RetryDisposition.DO_NOT_RETRY, safe_message=PYTHON_BINDING_FIXTURE)
    assert DomainError.model_validate_json(model.model_dump_json()) == model
    instant = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert UtcInstant.model_validate_json(instant.model_dump_json()) == instant
    assert OperationId.model_validate_json(OperationId(value="op").model_dump_json()) == OperationId(value="op")
