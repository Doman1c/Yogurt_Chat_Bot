"""The authoritative dispatch gate is anchored to a durable operation intent."""

from enum import Enum
from typing import Optional

from pydantic import model_validator

from ..execution.authorization_snapshot import OperationAuthorizationSnapshot
from ..foundation.durability import CorePersistenceMode, DurabilityLevel, durability_satisfies
from ..foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from ..foundation.identifiers import GenerationAuthorityId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..runtime.fencing import FencingDecision, validate_bound_provider
from ..runtime.generation import GenerationAssignment
from ..runtime.identity import RuntimeIdentity
from .intent import LogicalOperation, OperationIntentCommitted


class OperationDispatchValidationDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class OperationDispatchValidationResult(FrozenContract):
    decision: OperationDispatchValidationDecision
    error: Optional[DomainError] = None

    @model_validator(mode="after")
    def has_consistent_decision_and_error(self) -> "OperationDispatchValidationResult":
        if self.decision == OperationDispatchValidationDecision.ALLOW and self.error is not None:
            raise ValueError("an allowed dispatch result must not carry an error")
        if self.decision == OperationDispatchValidationDecision.DENY and self.error is None:
            raise ValueError("a denied dispatch result requires a typed error")
        return self

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
    intent: OperationIntentCommitted,
    current_authorization: OperationAuthorizationSnapshot,
    runtime_identity: RuntimeIdentity,
    current_generation_assignment: GenerationAssignment,
    expected_generation_authority: GenerationAuthorityId,
    dispatch_at: UtcInstant,
    achieved_durability: DurabilityLevel,
    execution_mode: CorePersistenceMode,
) -> OperationDispatchValidationResult:
    """Validate dispatch from immutable intent state plus current authorization and fencing evidence."""

    operation = intent.operation
    binding = intent.provider_binding
    if operation.deadline.expires_at.value < dispatch_at.value:
        return _deny(ErrorCode.DEADLINE_EXCEEDED, ErrorCategory.TIMEOUT, RetryDisposition.DO_NOT_RETRY, "operation deadline exceeded")
    if current_authorization.operation_id != operation.operation_id:
        return _deny(ErrorCode.CONTRACT_VIOLATION, ErrorCategory.CONFLICT, RetryDisposition.DO_NOT_RETRY, "authorization operation identity mismatch")
    if operation.authority.operation_id is not None and operation.authority.operation_id != operation.operation_id:
        return _deny(ErrorCode.CONTRACT_VIOLATION, ErrorCategory.CONFLICT, RetryDisposition.DO_NOT_RETRY, "delegated authority operation identity mismatch")
    if not _authorization_is_not_widened(operation, current_authorization):
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.DO_NOT_RETRY, "authorization snapshot widens delegated authority")
    if current_authorization.claims.scope != operation.target_capability:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.DO_NOT_RETRY, "operation capability scope mismatch")
    if binding.capability_id != operation.target_capability:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_AFTER_REBIND, "provider binding capability mismatch")
    if current_authorization.claims.audience != binding.provider_instance_id:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_AFTER_REBIND, "authorization audience does not match provider")
    if current_authorization.checked_at.value > dispatch_at.value:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_SAME_ID, "authorization snapshot was checked after dispatch")
    if current_authorization.valid_until.value < dispatch_at.value or current_authorization.claims.valid_until.value < dispatch_at.value:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_SAME_ID, "authorization evidence expired")
    if binding.authority_valid_until.value < dispatch_at.value:
        return _deny(ErrorCode.FORBIDDEN, ErrorCategory.AUTHORIZATION, RetryDisposition.RETRY_AFTER_REBIND, "provider binding authority expired")
    if execution_mode == CorePersistenceMode.EPHEMERAL and operation.traits.is_externally_side_effecting():
        return _deny(ErrorCode.CONTRACT_VIOLATION, ErrorCategory.VALIDATION, RetryDisposition.DO_NOT_RETRY, "ephemeral core cannot dispatch external side effects")
    if not durability_satisfies(achieved_durability, intent.required_durability):
        return _deny(ErrorCode.CONTRACT_VIOLATION, ErrorCategory.VALIDATION, RetryDisposition.RETRY_SAME_ID, "achieved durability does not satisfy committed operation requirement")
    fencing = validate_bound_provider(binding, runtime_identity, current_generation_assignment, expected_generation_authority)
    if fencing != FencingDecision.ACCEPT:
        return _deny(ErrorCode.STALE_GENERATION, ErrorCategory.CONFLICT, RetryDisposition.RETRY_AFTER_REBIND, "provider generation assignment is stale")
    return OperationDispatchValidationResult.allow()
