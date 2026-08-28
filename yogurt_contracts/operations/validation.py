"""The single deterministic safety gate for operation dispatch eligibility."""

from enum import Enum
from typing import Optional

from ..capability.binding import CapabilityBinding
from ..execution.authorization_snapshot import OperationAuthorizationSnapshot
from ..foundation.durability import DurabilityLevel, durability_satisfies
from ..foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from ..foundation.identifiers import GenerationAuthorityId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..runtime.fencing import FencingDecision, validate_bound_provider
from ..runtime.generation import GenerationAssignment
from ..runtime.identity import RuntimeIdentity
from .intent import LogicalOperation


class OperationDispatchValidationDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class OperationDispatchValidationResult(FrozenContract):
    decision: OperationDispatchValidationDecision
    error: Optional[DomainError] = None

    @classmethod
    def allow(cls) -> "OperationDispatchValidationResult":
        return cls(decision=OperationDispatchValidationDecision.ALLOW)

    @classmethod
    def deny(cls, error: DomainError) -> "OperationDispatchValidationResult":
        return cls(decision=OperationDispatchValidationDecision.DENY, error=error)


def _deny(code: ErrorCode, category: ErrorCategory, retry: RetryDisposition, safe_message: str) -> OperationDispatchValidationResult:
    return OperationDispatchValidationResult.deny(DomainError(code=code, category=category, retry=retry, safe_message=safe_message))


def _authorization_is_not_widened(operation: LogicalOperation, authorization: OperationAuthorizationSnapshot) -> bool:
    delegated = operation.authority.claims
    checked = authorization.claims
    return (
        delegated.actor == checked.actor
        and delegated.subject == checked.subject
        and delegated.origin == checked.origin
        and delegated.audience == checked.audience
        and delegated.scope == checked.scope
        and delegated.grant_revision == checked.grant_revision
    )


def validate_operation_dispatch(
    operation: LogicalOperation,
    authorization: OperationAuthorizationSnapshot,
    binding: CapabilityBinding,
    runtime_identity: RuntimeIdentity,
    generation_assignment: GenerationAssignment,
    expected_generation_authority: GenerationAuthorityId,
    dispatch_at: UtcInstant,
    required_durability: DurabilityLevel,
    achieved_durability: DurabilityLevel,
) -> OperationDispatchValidationResult:
    """Return an allow/deny result; ordinary eligibility failures are never exceptions."""

    if operation.deadline.expires_at.value < dispatch_at.value:
        return _deny(ErrorCode.DEADLINE_EXCEEDED, ErrorCategory.TIMEOUT, RetryDisposition.DO_NOT_RETRY, "operation deadline exceeded")
    if authorization.operation_id != operation.operation_id:
        return _deny(ErrorCode.CONTRACT_VIOLATION, ErrorCategory.CONFLICT, RetryDisposition.DO_NOT_RETRY, "authorization operation identity mismatch")
    if operation.authority.operation_id is not None and operation.authority.operation_id != operation.operation_id:
        return _deny(ErrorCode.CONTRACT_VIOLATION, ErrorCategory.CONFLICT, RetryDisposition.DO_NOT_RETRY, "delegated authority operation identity mismatch")
    if not _authorization_is_not_widened(operation, authorization):
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.DO_NOT_RETRY, "authorization snapshot widens delegated authority")
    if authorization.claims.scope != operation.target_capability:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.DO_NOT_RETRY, "operation capability scope mismatch")
    if binding.capability_id != operation.target_capability:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_AFTER_REBIND, "provider binding capability mismatch")
    if authorization.claims.audience != binding.provider_instance_id:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_AFTER_REBIND, "authorization audience does not match provider")
    if authorization.checked_at.value > dispatch_at.value:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_SAME_ID, "authorization snapshot was checked after dispatch")
    if authorization.valid_until.value < dispatch_at.value:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_SAME_ID, "authorization snapshot expired")
    if authorization.claims.valid_until.value < dispatch_at.value:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_SAME_ID, "authorization claims expired")
    if binding.authority_valid_until.value < dispatch_at.value:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_AFTER_REBIND, "provider binding authority expired")
    if not durability_satisfies(achieved_durability, required_durability):
        return _deny(ErrorCode.CONTRACT_VIOLATION, ErrorCategory.VALIDATION, RetryDisposition.RETRY_SAME_ID, "achieved durability does not satisfy operation requirement")
    fencing = validate_bound_provider(binding, runtime_identity, generation_assignment, expected_generation_authority)
    if fencing != FencingDecision.ACCEPT:
        return _deny(ErrorCode.STALE_GENERATION, ErrorCategory.CONFLICT, RetryDisposition.RETRY_AFTER_REBIND, "provider generation assignment is stale")
    return OperationDispatchValidationResult.allow()
