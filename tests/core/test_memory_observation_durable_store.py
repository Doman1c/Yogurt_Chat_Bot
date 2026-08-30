"""R3-B SQLite durability for the atomic intent-to-causal-sequence association."""

import asyncio
from datetime import datetime, timezone
import json

import pytest

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.execution.authorization_snapshot import OperationAuthorizationSnapshot
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.identifiers import (
    BindingId, CapabilityId, ConversationId, CoreInstanceId, InputId, OperationId,
    PersonaId, RuntimeInstanceId, RuntimeSlotId, TurnId, UserId,
)
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority
from yogurt_contracts.memory.causal import MemoryCausalResolution, MemoryObservationOperation, MemoryReconciliationProof, memory_observation_fingerprint
from yogurt_contracts.memory.observations import UserContentObservation
from yogurt_contracts.memory.recall import memory_conversation_scope_ref
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_core.ports.state_store import CoreStateError
from yogurt_core.operations.models import NotDispatchedReason
from yogurt_core.operations.models import ProviderDispatchCertainty, ProviderDispatchResult
from yogurt_core.operations.engine import OperationEngine
from yogurt_core.memory.observation_operations import MemoryObservationOperationCoordinator
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
FUTURE = UtcInstant(value=datetime(2026, 2, 1, tzinfo=timezone.utc))
CAPABILITY = CapabilityId(value="memory.observe")
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))


def _semantic(operation: str, domain: str = "legacy-v5") -> MemoryObservationOperation:
    observation = UserContentObservation(operation_id=OperationId(value=operation), conversation_ref=CONVERSATION, turn_ref=TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn-" + operation)), input_id=InputId(value="input-" + operation), content="hello-" + operation, observed_at=NOW)
    return MemoryObservationOperation(operation_id=observation.operation_id, observation=observation, scope_ref=memory_conversation_scope_ref(CONVERSATION), logical_provider_domain=domain, observation_fingerprint=memory_observation_fingerprint(observation))


def _intent(operation: str) -> OperationIntentCommitted:
    claims = AuthorityClaims(actor=RuntimeInstanceId(value="core"), subject=CONVERSATION.user_id, origin="core", audience=RuntimeInstanceId(value="provider"), scope=CAPABILITY, grant_revision="grant", valid_until=FUTURE, authority_reference="authority")
    logical = LogicalOperation(operation_id=OperationId(value=operation), request_fingerprint=_semantic(operation).observation_fingerprint, target_capability=CAPABILITY, authority=DelegatedAuthority(claims=claims, operation_id=OperationId(value=operation)), deadline=Deadline(expires_at=FUTURE), traits=OperationTraits(effect_kind=EffectKind.WRITE, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.IRREVERSIBLE, cancellation_support=CancellationSupport.BEST_EFFORT), created_at=NOW)
    authorization = OperationAuthorizationSnapshot(operation_id=logical.operation_id, claims=claims, checked_at=NOW, valid_until=FUTURE)
    binding = CapabilityBinding(binding_id=BindingId(value="binding"), binding_revision="1", capability_id=CAPABILITY, provider_slot_id=RuntimeSlotId(value="memory-slot"), provider_instance_id=RuntimeInstanceId(value="provider"), provider_generation=1, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=FUTURE)
    return OperationIntentCommitted(operation=logical, provider_binding=binding, authorization_snapshot=authorization, required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=NOW)


def _non_memory_intent(operation: str) -> OperationIntentCommitted:
    intent = _intent(operation)
    capability = CapabilityId(value="other.capability")
    claims = intent.operation.authority.claims.model_copy(update={"scope": capability})
    logical = intent.operation.model_copy(update={
        "target_capability": capability,
        "authority": DelegatedAuthority(claims=claims, operation_id=intent.operation.operation_id),
    })
    return intent.model_copy(update={
        "operation": logical,
        "provider_binding": intent.provider_binding.model_copy(update={"capability_id": capability}),
        "authorization_snapshot": intent.authorization_snapshot.model_copy(update={"claims": claims}),
    })


def _intent_with_binding(
    operation: str,
    binding_id: str,
    runtime_instance_id: str,
    runtime_generation: int,
) -> OperationIntentCommitted:
    intent = _intent(operation)
    claims = intent.operation.authority.claims.model_copy(update={
        "audience": RuntimeInstanceId(value=runtime_instance_id),
    })
    binding = intent.provider_binding.model_copy(update={
        "binding_id": BindingId(value=binding_id),
        "binding_revision": str(runtime_generation),
        "provider_instance_id": RuntimeInstanceId(value=runtime_instance_id),
        "provider_generation": runtime_generation,
    })
    logical = intent.operation.model_copy(update={
        "authority": DelegatedAuthority(claims=claims, operation_id=intent.operation.operation_id),
    })
    return intent.model_copy(update={
        "operation": logical,
        "provider_binding": binding,
        "authorization_snapshot": intent.authorization_snapshot.model_copy(update={"claims": claims}),
    })


def _proof(operation: str, domain: str = "legacy-v5") -> MemoryReconciliationProof:
    return MemoryReconciliationProof(
        operation_id=OperationId(value=operation), logical_provider_domain=domain,
        runtime_instance_id=RuntimeInstanceId(value="provider-restarted"), runtime_generation=2,
        binding_id=BindingId(value="binding-restarted"), resolution_reference="provider-reconcile-proof",
    )


class _MemoryProvider:
    def __init__(self, certainty: ProviderDispatchCertainty = ProviderDispatchCertainty.SUCCESS) -> None:
        self.certainty = certainty
        self.calls = 0
        self.intents = []

    @property
    def capability_id(self) -> CapabilityId:
        return CAPABILITY

    async def dispatch(self, intent: OperationIntentCommitted) -> ProviderDispatchResult:
        self.calls += 1
        self.intents.append(intent)
        return ProviderDispatchResult(certainty=self.certainty)


def test_sqlite_binds_operation_intent_and_sequence_atomically_and_persists_restart(tmp_path) -> None:
    async def run() -> None:
        path = tmp_path / "memory-causal.db"
        store = SQLiteCoreStateStore(path)
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        one, intent_one = _semantic("one"), _intent("one")
        results = await asyncio.gather(*[
            store.commit_memory_observation_intent(token, one, intent_one) for _ in range(4)
        ])
        assert {result.value.sequence for result in results if result.value is not None} == {1}
        assert (await store.get_operation(OperationId(value="one"))).intent == intent_one
        forged_ref = ConversationRef(
            persona_id=CONVERSATION.persona_id, user_id=CONVERSATION.user_id,
            conversation_id=ConversationId(value="other-conversation"),
        )
        forged_semantic = one.model_copy(update={"scope_ref": memory_conversation_scope_ref(forged_ref)})
        with pytest.raises(CoreStateError, match="MEMORY_OPERATION_SEMANTIC_CONFLICT"):
            await store.commit_memory_observation_intent(token, forged_semantic, intent_one)
        first_operation = await store.get_operation(OperationId(value="one"))
        await store.mark_not_dispatched(token, OperationId(value="one"), first_operation.revision, reason=NotDispatchedReason.POLICY_REJECTED)
        projected = await store.project_memory_operation_outcome(token, one)
        assert projected.value is not None and not projected.value.state.history_complete
        two = _semantic("two")
        assert (await store.commit_memory_observation_intent(token, two, _intent("two"))).value.sequence == 2
        with pytest.raises(CoreStateError, match="CROSS_DOMAIN_UNCERTAINTY_REJECTED"):
            await store.commit_memory_observation_intent(token, _semantic("other", domain="other-domain"), _intent("other"))
        await store.release_authority(token)
        await store.close()

        restarted = SQLiteCoreStateStore(path)
        restarted_token = await restarted.acquire_authority(CoreInstanceId(value="core-restarted"))
        before_rebuild = await restarted.get_memory_causal_lane(one)
        await restarted.rebuild_projections(restarted_token)
        lane = await restarted.get_memory_causal_lane(one)
        assert lane == before_rebuild
        assert lane.state.next_sequence == 3 and tuple(item.sequence for item in lane.operations) == (1, 2)
        assert not lane.state.history_complete
        await restarted.release_authority(restarted_token)
    asyncio.run(run())


def test_coordinator_reuses_r2_operation_engine_without_blocking_core_on_memory_lane(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "memory-operation-engine.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        semantic, intent = _semantic("coordinated"), _intent("coordinated")
        result = await MemoryObservationOperationCoordinator(store, OperationEngine(store)).execute(
            token, semantic, intent, _MemoryProvider(),
        )
        assert result.causal_operation.sequence == 1
        assert result.durable_operation.state.value == "succeeded"
        assert (await store.get_memory_causal_lane(semantic)).state.applied_through_sequence == 1
        await store.release_authority(token)
    asyncio.run(run())


def test_memory_bridge_rejects_same_fingerprint_non_memory_operation_id_collision(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "memory-operation-id-collision.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        semantic = _semantic("collision")
        existing_intent = _non_memory_intent("collision")
        await store.commit_operation_intent(token, existing_intent)
        existing = await store.get_operation(semantic.operation_id)
        await store.mark_dispatch_attempted(token, semantic.operation_id, existing.revision)
        attempted = await store.get_operation(semantic.operation_id)
        await store.mark_succeeded(token, semantic.operation_id, attempted.revision)
        succeeded = await store.get_operation(semantic.operation_id)

        with pytest.raises(CoreStateError, match="MEMORY_OPERATION_INTENT_CONFLICT"):
            await store.commit_memory_observation_intent(token, semantic, _intent("collision"))

        assert await store.get_operation(semantic.operation_id) == succeeded
        with pytest.raises(CoreStateError, match="MEMORY_CAUSAL_LANE_NOT_FOUND"):
            await store.get_memory_causal_lane(semantic)
        await store.release_authority(token)
    asyncio.run(run())


def test_uncertain_memory_lane_disables_recall_but_later_core_work_is_not_blocked(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "memory-unknown.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        coordinator = MemoryObservationOperationCoordinator(store, OperationEngine(store))
        unknown_provider = _MemoryProvider(ProviderDispatchCertainty.OUTCOME_UNKNOWN)
        first = await coordinator.execute(token, _semantic("unknown"), _intent("unknown"), unknown_provider)
        blocked_provider = _MemoryProvider()
        second = await coordinator.execute(token, _semantic("later"), _intent("later"), blocked_provider)
        lane = await store.get_memory_causal_lane(_semantic("unknown"))
        assert first.durable_operation.state.value == "outcome_unknown" and unknown_provider.calls == 1
        assert second.durable_operation.state.value == "intent_committed" and blocked_provider.calls == 0
        assert lane.state.first_unresolved_sequence == 1 and lane.state.history_complete
        await store.reconcile_memory_observation(token, _semantic("unknown"), MemoryCausalResolution.APPLIED, _proof("unknown"))
        retried = await coordinator.execute(token, _semantic("later"), _intent("later"), blocked_provider)
        assert retried.durable_operation.state.value == "succeeded" and blocked_provider.calls == 1
        await store.release_authority(token)
    asyncio.run(run())


def test_retry_durably_commits_restarted_binding_before_memory_provider_dispatch(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "memory-retry-binding.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        coordinator = MemoryObservationOperationCoordinator(store, OperationEngine(store))
        unknown = _semantic("unknown")
        await coordinator.execute(token, unknown, _intent("unknown"), _MemoryProvider(ProviderDispatchCertainty.OUTCOME_UNKNOWN))

        later = _semantic("later")
        generation_one = _intent_with_binding("later", "later-binding-1", "provider-generation-1", 1)
        blocked_provider = _MemoryProvider()
        blocked = await coordinator.execute(token, later, generation_one, blocked_provider)
        assert blocked.durable_operation.state.value == "intent_committed" and blocked_provider.calls == 0
        assert await store.get_memory_dispatch_attempts(later.operation_id) == ()

        await store.reconcile_memory_observation(token, unknown, MemoryCausalResolution.APPLIED, _proof("unknown"))
        generation_two = _intent_with_binding("later", "later-binding-2", "provider-generation-2", 2)
        retry_provider = _MemoryProvider()
        retried = await coordinator.execute(token, later, generation_two, retry_provider)
        attempts = await store.get_memory_dispatch_attempts(later.operation_id)

        assert retried.durable_operation.state.value == "succeeded"
        assert len(attempts) == 1 and attempts[0].dispatch_attempt.attempt_number == 1
        assert attempts[0].provider_binding == generation_two.provider_binding
        assert retry_provider.intents == [generation_two]
        assert retry_provider.intents[0].provider_binding == attempts[0].provider_binding
        assert (await store.get_operation(later.operation_id)).intent.provider_binding == generation_one.provider_binding
        await store.release_authority(token)
    asyncio.run(run())


def test_attempt_commit_survives_crash_before_memory_provider_call(tmp_path) -> None:
    async def run() -> None:
        path = tmp_path / "memory-attempt-crash.db"
        semantic = _semantic("crash")
        intent = _intent_with_binding("crash", "crash-binding-2", "provider-generation-2", 2)
        first = SQLiteCoreStateStore(path)
        token = await first.acquire_authority(CoreInstanceId(value="core"))
        await first.commit_memory_observation_intent(token, semantic, intent)
        committed = await first.commit_memory_dispatch_attempt(token, semantic, intent)
        assert committed.value is not None and committed.certainty.value == "committed"
        assert (await first.get_operation(semantic.operation_id)).state.value == "intent_committed"
        await first.release_authority(token)
        await first.close()

        restarted = SQLiteCoreStateStore(path)
        token = await restarted.acquire_authority(CoreInstanceId(value="restarted"))
        await restarted.rebuild_projections(token)
        attempts = await restarted.get_memory_dispatch_attempts(semantic.operation_id)
        assert len(attempts) == 1
        assert attempts[0].provider_binding == intent.provider_binding
        assert attempts[0].dispatch_attempt.attempt_number == 1
        assert (await restarted.get_operation(semantic.operation_id)).state.value == "intent_committed"
        await restarted.release_authority(token)
    asyncio.run(run())


def test_same_domain_restart_reconciliation_releases_unresolved_lane(tmp_path) -> None:
    async def run() -> None:
        path = tmp_path / "memory-reconcile.db"
        semantic, intent = _semantic("unknown"), _intent("unknown")
        first = SQLiteCoreStateStore(path)
        token = await first.acquire_authority(CoreInstanceId(value="core"))
        await first.commit_memory_observation_intent(token, semantic, intent)
        operation = await first.get_operation(semantic.operation_id)
        await first.mark_dispatch_attempted(token, semantic.operation_id, operation.revision)
        attempted = await first.get_operation(semantic.operation_id)
        await first.mark_outcome_unknown(token, semantic.operation_id, attempted.revision)
        await first.project_memory_operation_outcome(token, semantic)
        await first.release_authority(token)
        await first.close()

        restarted = SQLiteCoreStateStore(path)
        token = await restarted.acquire_authority(CoreInstanceId(value="restarted"))
        assert (await restarted.get_memory_causal_lane(semantic)).state.first_unresolved_sequence == 1
        reconciled = await restarted.reconcile_memory_observation(token, semantic, MemoryCausalResolution.APPLIED, _proof("unknown"))
        assert reconciled.value is not None and reconciled.value.state.first_unresolved_sequence is None
        assert reconciled.value.state.history_complete
        fact = restarted._connection.execute(
            "SELECT payload FROM facts WHERE fact_type = 'memory_causal_operation_reconciled'"
        ).fetchone()
        assert fact is not None
        assert json.loads(str(fact["payload"]))["reconciliation_proof"] == _proof("unknown").model_dump(mode="json")
        await restarted.release_authority(token)
    asyncio.run(run())


def test_reconciliation_requires_unknown_state_and_matching_domain_proof(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "memory-proof.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        pending = _semantic("pending")
        await store.commit_memory_observation_intent(token, pending, _intent("pending"))
        with pytest.raises(CoreStateError, match="REQUIRES_OUTCOME_UNKNOWN"):
            await store.reconcile_memory_observation(token, pending, MemoryCausalResolution.APPLIED, _proof("pending"))

        unknown = _semantic("unknown")
        await store.commit_memory_observation_intent(token, unknown, _intent("unknown"))
        operation = await store.get_operation(unknown.operation_id)
        await store.mark_dispatch_attempted(token, unknown.operation_id, operation.revision)
        attempted = await store.get_operation(unknown.operation_id)
        await store.mark_outcome_unknown(token, unknown.operation_id, attempted.revision)
        await store.project_memory_operation_outcome(token, unknown)
        with pytest.raises(CoreStateError, match="PROOF_MISMATCH"):
            await store.reconcile_memory_observation(token, unknown, MemoryCausalResolution.APPLIED, _proof("unknown", "other-domain"))
        for resolution in (MemoryCausalResolution.UNRESOLVED, MemoryCausalResolution.PENDING):
            with pytest.raises(CoreStateError, match="RESOLUTION_INVALID"):
                await store.reconcile_memory_observation(token, unknown, resolution, _proof("unknown"))
        await store.release_authority(token)
    asyncio.run(run())


@pytest.mark.parametrize("case", ["operation_id", "capability", "fingerprint", "subject", "turn"])
def test_semantic_observation_rejects_mismatched_durable_intent(case: str) -> None:
    semantic, intent = _semantic("bound"), _intent("bound")
    operation = intent.operation
    if case == "operation_id":
        operation = operation.model_copy(update={"operation_id": OperationId(value="other")})
    elif case == "capability":
        operation = operation.model_copy(update={"target_capability": CapabilityId(value="other.capability")})
    elif case == "fingerprint":
        operation = operation.model_copy(update={"request_fingerprint": RequestFingerprint(value="other-fingerprint")})
    elif case == "subject":
        claims = operation.authority.claims.model_copy(update={"subject": UserId(value="other-user")})
        operation = operation.model_copy(update={"authority": DelegatedAuthority(claims=claims, operation_id=operation.operation_id)})
    else:
        operation = operation.model_copy(update={"turn_ref": TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="other-turn"))})
    bad = intent.model_copy(update={"operation": operation})
    with pytest.raises(ValueError):
        semantic.validate_durable_intent(bad)
