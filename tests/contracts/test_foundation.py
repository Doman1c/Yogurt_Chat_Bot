from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from yogurt_contracts.foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from yogurt_contracts.foundation.identifiers import OperationId, TurnId
from yogurt_contracts.foundation.time import UtcInstant


def test_ids_are_distinct_frozen_value_objects():
    operation = OperationId(value="operation-1")
    turn = TurnId(value="turn-1")
    assert type(operation) is not type(turn)
    with pytest.raises(ValidationError):
        OperationId(value="bad\nvalue")
    with pytest.raises(ValidationError):
        operation.value = "changed"


def test_time_rejects_naive_and_accepts_utc():
    with pytest.raises(ValidationError):
        UtcInstant(value=datetime(2026, 1, 1))
    assert UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc)).value.tzinfo == timezone.utc


def test_domain_error_is_strict_and_bounded():
    error = DomainError(code=ErrorCode.INVALID_ARGUMENT, category=ErrorCategory.VALIDATION, retry=RetryDisposition.DO_NOT_RETRY, safe_message="bad input")
    assert error.safe_message == "bad input"
    with pytest.raises(ValidationError):
        DomainError(code=ErrorCode.INTERNAL, category=ErrorCategory.INTERNAL, retry=RetryDisposition.DO_NOT_RETRY, safe_message="x" * 513)
