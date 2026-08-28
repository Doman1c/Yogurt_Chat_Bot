"""Typed, behavioral operation dispatch conformance."""

from datetime import datetime, timezone

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.conversation.refs import TurnRef
from yogurt_contracts.execution.authorization_snapshot import OperationAuthorizationSnapshot
from yogurt_contracts.foundation.durability import CorePersistenceMode, DurabilityLevel
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.identifiers import BindingId, CapabilityId, ConversationId, GenerationAuthorityId, NodeId, OperationId, RuntimeInstanceId, RuntimeSlotId, TurnId, UserId
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_contracts.runtime.generation import GenerationAssignment
from yogurt_contracts.runtime.identity import RuntimeIdentity
from yogurt_conformance.protocols.adapters import OperationProviderAdapterProtocol


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
AUTHORITY = GenerationAuthorityId(value="authority")


def _typed_inputs() -> tuple[OperationIntentCommitted, OperationAuthorizationSnapshot, RuntimeIdentity, GenerationAssignment]:
    capability = CapabilityId(value="capability")
    instance = RuntimeInstanceId(value="provider")
    binding = CapabilityBinding(binding_id=BindingId(value="binding"), binding_revision="1", capability_id=capability, provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=instance, provider_generation=1, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=NOW)
    claims = AuthorityClaims(actor=RuntimeInstanceId(value="actor"), subject=UserId(value="user"), origin="surface", audience=instance, scope=capability, grant_revision="1", valid_until=NOW, authority_reference="authority")
    operation = LogicalOperation(operation_id=OperationId(value="operation"), request_fingerprint=RequestFingerprint(value="fingerprint"), target_capability=capability, authority=DelegatedAuthority(claims=claims, operation_id=OperationId(value="operation")), deadline=Deadline(expires_at=NOW), traits=OperationTraits(effect_kind=EffectKind.WRITE, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.COMPENSATABLE, cancellation_support=CancellationSupport.BEST_EFFORT), created_at=NOW, turn_ref=TurnRef(conversation_id=ConversationId(value="conversation"), turn_id=TurnId(value="turn")))
    authorization = OperationAuthorizationSnapshot(operation_id=operation.operation_id, claims=claims, checked_at=NOW, valid_until=NOW)
    intent = OperationIntentCommitted(operation=operation, provider_binding=binding, authorization_snapshot=authorization, required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=NOW)
    assignment = GenerationAssignment(runtime_slot_id=binding.provider_slot_id, runtime_instance_id=instance, generation=1, issued_by=AUTHORITY, issued_at=NOW, authority_revision="1")
    runtime = RuntimeIdentity(runtime_type="provider", runtime_slot_id=binding.provider_slot_id, runtime_instance_id=instance, runtime_generation=1, node_id=NodeId(value="node"))
    return intent, authorization, runtime, assignment


def run_operation_provider_conformance(adapter: OperationProviderAdapterProtocol) -> None:
    intent, authorization, runtime, assignment = _typed_inputs()
    try: adapter.dispatch(intent, authorization, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE)
    except (KeyError, ValueError): pass
    else: raise AssertionError("dispatch requires committed intent")
    committed = adapter.commit_intent(intent)
    assert committed.operation_id == intent.operation.operation_id and committed.side_effect_count == 0
    assert adapter.commit_intent(intent).operation_id == intent.operation.operation_id
    def assert_blocked(state: object) -> None:
        assert getattr(state, "attempts") == ()
        assert getattr(state, "side_effect_count") == 0
    assert_blocked(adapter.dispatch(intent, authorization, runtime, assignment, AUTHORITY, DurabilityLevel.JOURNALED, CorePersistenceMode.DURABLE))
    assert_blocked(adapter.dispatch(intent, authorization, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.EPHEMERAL))
    wrong_scope = authorization.model_copy(update={"claims": authorization.claims.model_copy(update={"scope": CapabilityId(value="wrong")})})
    assert_blocked(adapter.dispatch(intent, wrong_scope, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE))
    wrong_audience = authorization.model_copy(update={"claims": authorization.claims.model_copy(update={"audience": RuntimeInstanceId(value="wrong-audience")})})
    assert_blocked(adapter.dispatch(intent, wrong_audience, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE))
    expired_authorization = authorization.model_copy(update={"valid_until": UtcInstant(value=datetime(2025, 12, 31, tzinfo=timezone.utc))})
    assert_blocked(adapter.dispatch(intent, expired_authorization, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE))
    expired_binding = intent.model_copy(update={"provider_binding": intent.provider_binding.model_copy(update={"authority_valid_until": UtcInstant(value=datetime(2025, 12, 31, tzinfo=timezone.utc))})})
    assert_blocked(adapter.dispatch(expired_binding, authorization, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE))
    assert_blocked(adapter.dispatch(intent, authorization, runtime, assignment, GenerationAuthorityId(value="wrong-authority"), DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE))
    stale = assignment.model_copy(update={"generation": 2})
    assert_blocked(adapter.dispatch(intent, authorization, runtime, stale, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE))
    first = adapter.dispatch(intent, authorization, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE)
    second = adapter.dispatch(intent, authorization, runtime, assignment, AUTHORITY, DurabilityLevel.DURABLY_PERSISTED, CorePersistenceMode.DURABLE)
    assert len(first.attempts) == 1 and len(second.attempts) == 2 and first.attempts[0] != second.attempts[1] and second.side_effect_count == 1
    unknown = adapter.unknown_outcome(intent.operation.operation_id)
    assert unknown.operation_id == intent.operation.operation_id and unknown.outcome_unknown
