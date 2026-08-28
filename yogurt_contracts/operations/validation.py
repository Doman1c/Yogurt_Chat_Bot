from enum import Enum

from ..capability.binding import CapabilityBinding
from ..execution.authorization_snapshot import OperationAuthorizationSnapshot
from ..foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from ..foundation.time import UtcInstant
from .intent import LogicalOperation


class OperationValidationDecision(str, Enum): ALLOW = "allow"; DENY = "deny"


def validate_operation_dispatch(operation: LogicalOperation, authorization: OperationAuthorizationSnapshot, binding: CapabilityBinding, dispatch_at: UtcInstant) -> OperationValidationDecision:
    invalid = (authorization.operation_id != operation.operation_id or operation.target_capability != authorization.claims.scope or operation.target_capability != binding.capability_id or authorization.claims.audience != binding.provider_instance_id or authorization.valid_until.value < dispatch_at.value or authorization.claims.valid_until.value < dispatch_at.value or binding.authority_valid_until.value < dispatch_at.value or operation.deadline.expires_at.value < dispatch_at.value or (operation.authority.operation_id is not None and operation.authority.operation_id != operation.operation_id))
    if invalid:
        raise ValueError(DomainError(code=ErrorCode.FORBIDDEN, category=ErrorCategory.AUTHORIZATION, retry=RetryDisposition.DO_NOT_RETRY, safe_message="operation authorization or binding validation failed").model_dump_json())
    return OperationValidationDecision.ALLOW
