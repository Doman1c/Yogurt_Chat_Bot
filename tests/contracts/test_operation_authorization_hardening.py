"""P0-03 authoritative durable-intent dispatch regression coverage."""

from datetime import datetime, timezone

import pytest

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.execution.authorization_snapshot import OperationAuthorizationSnapshot
from yogurt_contracts.foundation.durability import CorePersistenceMode, DurabilityLevel
from yogurt_contracts.foundation.errors import DomainError, ErrorCategory, ErrorCode, RetryDisposition
from yogurt_contracts.foundation.identifiers import BindingId, CapabilityId, GenerationAuthorityId, NodeId, OperationId, RuntimeInstanceId, RuntimeSlotId, UserId
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_contracts.operations.validation import OperationDispatchValidationDecision, OperationDispatchValidationResult, validate_operation_dispatch
from yogurt_contracts.runtime.generation import GenerationAssignment
from yogurt_contracts.runtime.identity import RuntimeIdentity


NOW = UtcInstant(value=datetime(2026, 1, 2, tzinfo=timezone.utc))
FUTURE = UtcInstant(value=datetime(2026, 1, 3, tzinfo=timezone.utc))
LATER = UtcInstant(value=datetime(2026, 1, 4, tzinfo=timezone.utc))
AUTHORITY = GenerationAuthorityId(value="management-generation-authority")
CAPABILITY = CapabilityId(value="robot.motion")


def _claims(valid_until: UtcInstant = FUTURE, scope: CapabilityId = CAPABILITY, audience: RuntimeInstanceId = RuntimeInstanceId(value="provider")) -> AuthorityClaims:
    return AuthorityClaims(actor=RuntimeInstanceId(value="actor"), subject=UserId(value="user"), origin="surface", audience=audience, scope=scope, grant_revision="grant-1", valid_until=valid_until, authority_reference="opaque-reference")


def _context(effect_kind: EffectKind = EffectKind.WRITE, required: DurabilityLevel = DurabilityLevel.DURABLY_PERSISTED):
    claims = _claims()
    operation = LogicalOperation(operation_id=OperationId(value="operation"), request_fingerprint=RequestFingerprint(value="fingerprint-a"), target_capability=CAPABILITY, authority=DelegatedAuthority(claims=claims, operation_id=OperationId(value="operation")), deadline=Deadline(expires_at=FUTURE), traits=OperationTraits(effect_kind=effect_kind, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.COMPENSATABLE, cancellation_support=CancellationSupport.BEST_EFFORT), created_at=NOW)
    authorization = OperationAuthorizationSnapshot(operation_id=operation.operation_id, claims=claims, checked_at=NOW, valid_until=FUTURE)
    binding = CapabilityBinding(binding_id=BindingId(value="binding"), binding_revision="1", capability_id=CAPABILITY, provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=RuntimeInstanceId(value="provider"), provider_generation=7, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=FUTURE)
    intent = OperationIntentCommitted(operation=operation, provider_binding=binding, authorization_snapshot=authorization, required_durability=required, committed_at=NOW)
    runtime = RuntimeIdentity(runtime_type="provider", runtime_slot_id=binding.provider_slot_id, runtime_instance_id=binding.provider_instance_id, runtime_generation=7, node_id=NodeId(value="node"))
    assignment = GenerationAssignment(runtime_slot_id=runtime.runtime_slot_id, runtime_instance_id=runtime.runtime_instance_id, generation=7, issued_by=AUTHORITY, issued_at=NOW, authority_revision="1")
    return intent, authorization, runtime, assignment


def _validate(intent, authorization, runtime, assignment, achieved: DurabilityLevel = DurabilityLevel.DURABLY_PERSISTED, mode: CorePersistenceMode = CorePersistenceMode.DURABLE, dispatch_at: UtcInstant = NOW):
    return validate_operation_dispatch(intent, authorization, runtime, assignment, AUTHORITY, dispatch_at, achieved, mode)


def test_authoritative_gate_accepts_a_valid_intent() -> None:
    assert _validate(*_context()).decision == OperationDispatchValidationDecision.ALLOW


def test_gate_reads_required_durability_and_binding_only_from_intent() -> None:
    intent, authorization, runtime, assignment = _context(required=DurabilityLevel.DURABLY_PERSISTED)
    result = _validate(intent, authorization, runtime, assignment, achieved=DurabilityLevel.JOURNALED)
    assert result.decision == OperationDispatchValidationDecision.DENY
    assert result.error is not None and result.error.code == ErrorCode.CONTRACT_VIOLATION
    assert "required_durability" not in validate_operation_dispatch.__annotations__
    assert "binding" not in validate_operation_dispatch.__annotations__
    assert "operation" not in validate_operation_dispatch.__annotations__


def test_gate_cannot_accept_substituted_operation_or_request_fingerprint() -> None:
    intent, authorization, runtime, assignment = _context()
    replacement = intent.operation.model_copy(update={"request_fingerprint": RequestFingerprint(value="fingerprint-b")})
    assert replacement.operation_id == intent.operation.operation_id
    assert replacement.request_fingerprint != intent.operation.request_fingerprint
    assert "intent" in validate_operation_dispatch.__annotations__
    assert _validate(intent, authorization, runtime, assignment).decision == OperationDispatchValidationDecision.ALLOW


def test_refresh_uses_same_immutable_intent_operation_and_rejects_widening() -> None:
    intent, _, runtime, assignment = _context()
    refreshed = OperationAuthorizationSnapshot(operation_id=intent.operation.operation_id, claims=_claims(), checked_at=NOW, valid_until=FUTURE)
    assert _validate(intent, refreshed, runtime, assignment).decision == OperationDispatchValidationDecision.ALLOW
    widened = refreshed.model_copy(update={"claims": _claims(scope=CapabilityId(value="memory.read"))})
    denied = _validate(intent, widened, runtime, assignment)
    assert denied.decision == OperationDispatchValidationDecision.DENY


@pytest.mark.parametrize("case", ["operation_id", "scope", "audience", "expired"])
def test_current_authorization_mismatches_are_denied(case: str) -> None:
    intent, authorization, runtime, assignment = _context()
    if case == "operation_id":
        current = authorization.model_copy(update={"operation_id": OperationId(value="other")})
    elif case == "scope":
        current = OperationAuthorizationSnapshot(operation_id=intent.operation.operation_id, claims=_claims(scope=CapabilityId(value="memory.read")), checked_at=NOW, valid_until=FUTURE)
    elif case == "audience":
        current = OperationAuthorizationSnapshot(operation_id=intent.operation.operation_id, claims=_claims(audience=RuntimeInstanceId(value="other-provider")), checked_at=NOW, valid_until=FUTURE)
    else:
        expired = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
        current = OperationAuthorizationSnapshot(operation_id=intent.operation.operation_id, claims=_claims(), checked_at=expired, valid_until=expired)
    assert _validate(intent, current, runtime, assignment).decision == OperationDispatchValidationDecision.DENY


def test_deadline_and_invalid_committed_binding_are_rejected() -> None:
    intent, authorization, runtime, assignment = _context()
    assert _validate(intent, authorization, runtime, assignment, dispatch_at=LATER).decision == OperationDispatchValidationDecision.DENY
    invalid_binding = intent.provider_binding.model_copy(update={"capability_id": CapabilityId(value="memory.read")})
    with pytest.raises(ValueError):
        OperationIntentCommitted(operation=intent.operation, provider_binding=invalid_binding, authorization_snapshot=intent.authorization_snapshot, required_durability=intent.required_durability, committed_at=NOW)


@pytest.mark.parametrize("effect_kind", [EffectKind.WRITE, EffectKind.PHYSICAL])
def test_ephemeral_core_rejects_external_side_effects_even_with_durable_storage(effect_kind: EffectKind) -> None:
    intent, authorization, runtime, assignment = _context(effect_kind=effect_kind)
    result = _validate(intent, authorization, runtime, assignment, mode=CorePersistenceMode.EPHEMERAL)
    assert result.decision == OperationDispatchValidationDecision.DENY
    assert result.error is not None and result.error.code == ErrorCode.CONTRACT_VIOLATION


def test_durable_core_allows_read_only_and_requires_committed_durability() -> None:
    intent, authorization, runtime, assignment = _context(effect_kind=EffectKind.READ, required=DurabilityLevel.JOURNALED)
    assert _validate(intent, authorization, runtime, assignment, achieved=DurabilityLevel.JOURNALED).decision == OperationDispatchValidationDecision.ALLOW
    assert _validate(intent, authorization, runtime, assignment, achieved=DurabilityLevel.JOURNALED, mode=CorePersistenceMode.EPHEMERAL).decision == OperationDispatchValidationDecision.ALLOW
    assert _validate(intent, authorization, runtime, assignment, achieved=DurabilityLevel.EPHEMERAL_COMMITTED).decision == OperationDispatchValidationDecision.DENY


def test_old_intent_binding_is_rejected_after_provider_restart() -> None:
    intent, authorization, _, _ = _context()
    current_runtime = RuntimeIdentity(runtime_type="provider", runtime_slot_id=RuntimeSlotId(value="slot"), runtime_instance_id=RuntimeInstanceId(value="provider-new"), runtime_generation=8, node_id=NodeId(value="node"))
    current_assignment = GenerationAssignment(runtime_slot_id=current_runtime.runtime_slot_id, runtime_instance_id=current_runtime.runtime_instance_id, generation=8, issued_by=AUTHORITY, issued_at=NOW, authority_revision="2")
    result = _validate(intent, authorization, current_runtime, current_assignment)
    assert result.decision == OperationDispatchValidationDecision.DENY
    assert result.error is not None and result.error.code == ErrorCode.STALE_GENERATION
    assert result.error.retry == RetryDisposition.RETRY_AFTER_REBIND


def test_result_model_requires_consistent_decision_and_typed_error() -> None:
    error = DomainError(code=ErrorCode.FORBIDDEN, category=ErrorCategory.AUTHORIZATION, retry=RetryDisposition.DO_NOT_RETRY, safe_message="denied")
    with pytest.raises(ValueError):
        OperationDispatchValidationResult(decision=OperationDispatchValidationDecision.ALLOW, error=error)
    with pytest.raises(ValueError):
        OperationDispatchValidationResult(decision=OperationDispatchValidationDecision.DENY)
