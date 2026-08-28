"""P0-03 dispatch authorization, binding, durability, and refresh regression coverage."""

from datetime import datetime, timezone

import pytest

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.execution.authorization_snapshot import OperationAuthorizationSnapshot
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.errors import ErrorCode
from yogurt_contracts.foundation.identifiers import BindingId, CapabilityId, GenerationAuthorityId, OperationId, RuntimeInstanceId, RuntimeSlotId, UserId
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_contracts.operations.validation import OperationDispatchValidationDecision, validate_operation_dispatch
from yogurt_contracts.runtime.generation import GenerationAssignment
from yogurt_contracts.runtime.identity import RuntimeIdentity
from yogurt_contracts.foundation.identifiers import NodeId


NOW = UtcInstant(value=datetime(2026, 1, 2, tzinfo=timezone.utc))
PAST = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
FUTURE = UtcInstant(value=datetime(2026, 1, 3, tzinfo=timezone.utc))
LATER = UtcInstant(value=datetime(2026, 1, 4, tzinfo=timezone.utc))
AUTHORITY = GenerationAuthorityId(value="management-generation-authority")
CAPABILITY = CapabilityId(value="robot.motion")


def _claims(valid_until: UtcInstant = FUTURE, audience: RuntimeInstanceId = RuntimeInstanceId(value="provider"), scope: CapabilityId = CAPABILITY) -> AuthorityClaims:
    return AuthorityClaims(actor=RuntimeInstanceId(value="actor"), subject=UserId(value="user"), origin="surface", audience=audience, scope=scope, grant_revision="grant-1", valid_until=valid_until, authority_reference="opaque-reference")


def _context():
    claims = _claims()
    operation = LogicalOperation(
        operation_id=OperationId(value="operation"), request_fingerprint=RequestFingerprint(value="fingerprint"), target_capability=CAPABILITY,
        authority=DelegatedAuthority(claims=claims, operation_id=OperationId(value="operation")), deadline=Deadline(expires_at=FUTURE),
        traits=OperationTraits(effect_kind=EffectKind.WRITE, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.COMPENSATABLE, cancellation_support=CancellationSupport.BEST_EFFORT), created_at=NOW,
    )
    authorization = OperationAuthorizationSnapshot(operation_id=operation.operation_id, claims=claims, checked_at=NOW, valid_until=FUTURE)
    binding = CapabilityBinding(binding_id=BindingId(value="binding"), binding_revision="1", capability_id=CAPABILITY, provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=RuntimeInstanceId(value="provider"), provider_generation=7, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=FUTURE)
    runtime = RuntimeIdentity(runtime_type="provider", runtime_slot_id=binding.provider_slot_id, runtime_instance_id=binding.provider_instance_id, runtime_generation=7, node_id=NodeId(value="node"))
    assignment = GenerationAssignment(runtime_slot_id=runtime.runtime_slot_id, runtime_instance_id=runtime.runtime_instance_id, generation=runtime.runtime_generation, issued_by=AUTHORITY, issued_at=NOW, authority_revision="1")
    return operation, authorization, binding, runtime, assignment


def _validate(operation, authorization, binding, runtime, assignment, dispatch_at: UtcInstant = NOW, achieved: DurabilityLevel = DurabilityLevel.DURABLY_PERSISTED):
    return validate_operation_dispatch(operation, authorization, binding, runtime, assignment, AUTHORITY, dispatch_at, DurabilityLevel.DURABLY_PERSISTED, achieved)


def test_matching_operation_authorization_binding_and_assignment_are_allowed() -> None:
    assert _validate(*_context()).decision == OperationDispatchValidationDecision.ALLOW


def test_operation_intent_persists_authorization_snapshot_for_recovery() -> None:
    operation, authorization, binding, _, _ = _context()
    intent = OperationIntentCommitted(operation=operation, provider_binding=binding, authorization_snapshot=authorization, required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=NOW)
    restored = OperationIntentCommitted.model_validate(intent.model_dump())
    assert restored.operation.operation_id == operation.operation_id
    assert restored.authorization_snapshot == authorization
    assert restored.provider_binding == binding


@pytest.mark.parametrize(
    "case",
    ["operation_id", "scope", "binding_capability", "audience", "authorization_expired", "binding_expired", "delegated_operation", "widened", "stale_generation"],
)
def test_dispatch_denies_authorization_and_binding_mismatches(case: str) -> None:
    operation, authorization, binding, runtime, assignment = _context()
    if case == "operation_id":
        authorization = authorization.model_copy(update={"operation_id": OperationId(value="other")})
    elif case == "scope":
        denied_claims = _claims(scope=CapabilityId(value="memory.read"))
        operation = operation.model_copy(update={"authority": DelegatedAuthority(claims=denied_claims, operation_id=operation.operation_id)})
        authorization = authorization.model_copy(update={"claims": denied_claims})
    elif case == "binding_capability":
        binding = binding.model_copy(update={"capability_id": CapabilityId(value="memory.read")})
    elif case == "audience":
        denied_claims = _claims(audience=RuntimeInstanceId(value="other-provider"))
        operation = operation.model_copy(update={"authority": DelegatedAuthority(claims=denied_claims, operation_id=operation.operation_id)})
        authorization = authorization.model_copy(update={"claims": denied_claims})
    elif case == "authorization_expired":
        authorization = OperationAuthorizationSnapshot(operation_id=operation.operation_id, claims=_claims(valid_until=FUTURE), checked_at=PAST, valid_until=PAST)
    elif case == "binding_expired":
        binding = binding.model_copy(update={"authority_valid_until": PAST})
    elif case == "delegated_operation":
        operation = operation.model_copy(update={"authority": DelegatedAuthority(claims=operation.authority.claims, operation_id=OperationId(value="other"))})
    elif case == "widened":
        authorization = authorization.model_copy(update={"claims": _claims(audience=RuntimeInstanceId(value="other-provider"))})
    else:
        assignment = assignment.model_copy(update={"generation": 6})
    result = _validate(operation, authorization, binding, runtime, assignment)
    assert result.decision == OperationDispatchValidationDecision.DENY
    assert result.error is not None


def test_claims_expiry_is_typed_authorization_denial() -> None:
    operation, _, binding, runtime, assignment = _context()
    expired_claims = _claims(valid_until=PAST)
    operation = operation.model_copy(update={"authority": DelegatedAuthority(claims=expired_claims, operation_id=operation.operation_id)})
    authorization = OperationAuthorizationSnapshot(operation_id=operation.operation_id, claims=expired_claims, checked_at=PAST, valid_until=PAST)
    result = _validate(operation, authorization, binding, runtime, assignment)
    assert result.decision == OperationDispatchValidationDecision.DENY
    assert result.error is not None
    assert result.error.code == ErrorCode.FORBIDDEN


def test_operation_deadline_expiry_is_not_collapsed_into_forbidden() -> None:
    operation, authorization, binding, runtime, assignment = _context()
    result = _validate(operation, authorization, binding, runtime, assignment, dispatch_at=LATER)
    assert result.decision == OperationDispatchValidationDecision.DENY
    assert result.error is not None
    assert result.error.code == ErrorCode.DEADLINE_EXCEEDED


def test_authorization_refresh_keeps_the_same_operation_id() -> None:
    operation, _, binding, runtime, assignment = _context()
    first_claims = _claims(valid_until=NOW)
    first_snapshot = OperationAuthorizationSnapshot(operation_id=operation.operation_id, claims=first_claims, checked_at=PAST, valid_until=NOW)
    stale_operation = operation.model_copy(update={"authority": DelegatedAuthority(claims=first_claims, operation_id=operation.operation_id)})
    intent = OperationIntentCommitted(operation=stale_operation, provider_binding=binding, authorization_snapshot=first_snapshot, required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=PAST)
    refreshed_claims = _claims(valid_until=FUTURE)
    refreshed_operation = stale_operation.model_copy(update={"authority": DelegatedAuthority(claims=refreshed_claims, operation_id=stale_operation.operation_id)})
    refreshed_snapshot = OperationAuthorizationSnapshot(operation_id=intent.operation.operation_id, claims=refreshed_claims, checked_at=NOW, valid_until=FUTURE)
    result = _validate(refreshed_operation, refreshed_snapshot, binding, runtime, assignment)
    assert refreshed_snapshot.operation_id == intent.operation.operation_id
    assert result.decision == OperationDispatchValidationDecision.ALLOW
