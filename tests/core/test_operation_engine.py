import asyncio
from datetime import datetime, timezone

import pytest

from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.execution.authorization_snapshot import OperationAuthorizationSnapshot
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.durability import CorePersistenceMode
from yogurt_contracts.foundation.identifiers import BindingId, CapabilityId, CoreInstanceId, GenerationAuthorityId, OperationId, RuntimeInstanceId, RuntimeSlotId, UserId
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind

from yogurt_core.operations.engine import OperationEngine
from yogurt_core.operations.models import DurableOperationState, NotDispatchedReason, ProviderDispatchCertainty, ProviderDispatchResult
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore
from yogurt_core.ports.state_store import CoreStateError


NOW = UtcInstant(value=datetime(2026, 1, 1, tzinfo=timezone.utc))
FUTURE = UtcInstant(value=datetime(2026, 2, 1, tzinfo=timezone.utc))


def _intent(operation_id: str, fingerprint: str, kind: IdempotencyKind = IdempotencyKind.KEYED) -> OperationIntentCommitted:
    capability = CapabilityId(value="fake.presentation")
    claims = AuthorityClaims(actor=RuntimeInstanceId(value="actor"), subject=UserId(value="user"), origin="test", audience=RuntimeInstanceId(value="provider"), scope=capability, grant_revision="grant", valid_until=FUTURE, authority_reference="authority")
    operation = LogicalOperation(operation_id=OperationId(value=operation_id), request_fingerprint=RequestFingerprint(value=fingerprint), target_capability=capability, authority=DelegatedAuthority(claims=claims, operation_id=OperationId(value=operation_id)), deadline=Deadline(expires_at=FUTURE), traits=OperationTraits(effect_kind=EffectKind.WRITE, idempotency=kind, reversibility=ReversibilityKind.IRREVERSIBLE, cancellation_support=CancellationSupport.BEST_EFFORT), created_at=NOW)
    authorization = OperationAuthorizationSnapshot(operation_id=operation.operation_id, claims=claims, checked_at=NOW, valid_until=FUTURE)
    binding = CapabilityBinding(binding_id=BindingId(value="binding"), binding_revision="1", capability_id=capability, provider_slot_id=RuntimeSlotId(value="slot"), provider_instance_id=RuntimeInstanceId(value="provider"), provider_generation=1, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=FUTURE)
    return OperationIntentCommitted(operation=operation, provider_binding=binding, authorization_snapshot=authorization, required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=NOW)


class FakeProvider:
    def __init__(self, store: SQLiteCoreStateStore, result: ProviderDispatchCertainty, capability_id: CapabilityId = CapabilityId(value="fake.presentation")) -> None:
        self.store, self.result, self.calls, self.visible = store, result, 0, False
        self._capability_id = capability_id

    @property
    def capability_id(self) -> CapabilityId:
        return self._capability_id

    async def dispatch(self, intent: OperationIntentCommitted) -> ProviderDispatchResult:
        self.calls += 1
        operation = await self.store.get_operation(intent.operation.operation_id)
        self.visible = operation.state == DurableOperationState.DISPATCH_ATTEMPTED
        return ProviderDispatchResult(certainty=self.result)


def test_operation_engine_durable_ordering_outcomes_and_guards(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "operations.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        engine = OperationEngine(store)
        provider = FakeProvider(store, ProviderDispatchCertainty.SUCCESS)
        succeeded = await engine.execute(token, _intent("success", "one"), provider)
        assert provider.visible and provider.calls == 1 and succeeded.state == DurableOperationState.SUCCEEDED
        failed = await engine.execute(token, _intent("failure", "two"), FakeProvider(store, ProviderDispatchCertainty.DEFINITE_FAILURE))
        assert failed.state == DurableOperationState.FAILED
        unknown_provider = FakeProvider(store, ProviderDispatchCertainty.OUTCOME_UNKNOWN)
        unknown = await engine.execute(token, _intent("unknown", "three", IdempotencyKind.NONE), unknown_provider)
        assert unknown.state == DurableOperationState.OUTCOME_UNKNOWN
        assert (await engine.execute(token, _intent("unknown", "three", IdempotencyKind.NONE), unknown_provider)).state == DurableOperationState.OUTCOME_UNKNOWN and unknown_provider.calls == 1
        guarded_provider = FakeProvider(store, ProviderDispatchCertainty.SUCCESS)
        guarded = await engine.execute(token, _intent("guard", "four"), guarded_provider, NotDispatchedReason.CANCELLED_BEFORE_DISPATCH)
        assert guarded.state == DurableOperationState.NOT_DISPATCHED and guarded_provider.calls == 0
    asyncio.run(run())


def test_operation_intent_precommit_failure_never_dispatches_provider(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "operation-intent-not-committed.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        provider = FakeProvider(store, ProviderDispatchCertainty.SUCCESS)
        intent = _intent("not-committed-operation", "not-committed")
        store.simulate_semantic_not_committed_once()
        with pytest.raises(CoreStateError, match="OPERATION_STATE_NOT_COMMITTED"):
            await OperationEngine(store).execute(token, intent, provider)
        assert provider.calls == 0
        with pytest.raises(CoreStateError, match="OPERATION_NOT_FOUND"):
            await store.get_operation(intent.operation.operation_id)
    asyncio.run(run())


def test_operation_identity_fencing_restart_and_projection_rebuild(tmp_path) -> None:
    async def run() -> None:
        path = tmp_path / "operation-restart.db"
        store = SQLiteCoreStateStore(path)
        old = await store.acquire_authority(CoreInstanceId(value="old"))
        intent = _intent("restart", "fingerprint")
        first = await store.commit_operation_intent(old, intent)
        assert first.value is not None and first.value.state == DurableOperationState.INTENT_COMMITTED
        same = await store.commit_operation_intent(old, intent)
        assert same.value is not None and same.value.revision == 1
        try:
            await store.commit_operation_intent(old, _intent("restart", "different"))
            assert False, "operation fingerprint conflict must fail"
        except CoreStateError as error:
            assert error.code == "OPERATION_ID_CONFLICT"
        await store.rebuild_projections(old)
        assert (await store.get_operation(OperationId(value="restart"))).state == DurableOperationState.INTENT_COMMITTED
        attempted = await store.mark_dispatch_attempted(old, OperationId(value="restart"), 1)
        assert attempted.value is not None and attempted.value.state == DurableOperationState.DISPATCH_ATTEMPTED
        await store.release_authority(old)
        current = await store.acquire_authority(CoreInstanceId(value="new"))
        try:
            await store.mark_failed(old, OperationId(value="restart"), 2)
            assert False, "stale authority must fail"
        except CoreStateError as error:
            assert error.code == "STALE_CORE_AUTHORITY"
        assert (await store.get_operation(OperationId(value="restart"))).state == DurableOperationState.DISPATCH_ATTEMPTED
        await store.rebuild_projections(current)
        assert (await store.get_operation(OperationId(value="restart"))).state == DurableOperationState.DISPATCH_ATTEMPTED
    asyncio.run(run())


def test_recovery_keeps_dispatched_operation_pending_without_provider_redrive(tmp_path) -> None:
    async def run() -> None:
        path = tmp_path / "operation-recovery.db"
        store = SQLiteCoreStateStore(path)
        token = await store.acquire_authority(CoreInstanceId(value="core-a"))
        intent = _intent("pending-operation", "pending")
        committed = await store.commit_operation_intent(token, intent)
        assert committed.value is not None
        attempted = await store.mark_dispatch_attempted(token, intent.operation.operation_id, committed.value.revision)
        assert attempted.value is not None
        await store.release_authority(token)
        await store.close()

        restarted = SQLiteCoreStateStore(path)
        recovery_token = await restarted.acquire_authority(CoreInstanceId(value="core-b"))
        await restarted.recover(recovery_token)
        operation = await restarted.get_operation(intent.operation.operation_id)
        assert operation.state == DurableOperationState.DISPATCH_ATTEMPTED
        await restarted.release_authority(recovery_token)
    asyncio.run(run())


def test_operation_zero_dispatch_guards_and_ephemeral_boundary(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "guards.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        engine = OperationEngine(store)
        for index, reason in enumerate((NotDispatchedReason.DEADLINE_EXCEEDED, NotDispatchedReason.PROVIDER_NOT_AVAILABLE, NotDispatchedReason.CAPABILITY_UNAVAILABLE, NotDispatchedReason.POLICY_REJECTED)):
            provider = FakeProvider(store, ProviderDispatchCertainty.SUCCESS)
            result = await engine.execute(token, _intent("guard-" + str(index), "guard-" + str(index)), provider, reason)
            assert result.state == DurableOperationState.NOT_DISPATCHED and result.not_dispatched_reason == reason and provider.calls == 0
        class EphemeralStore:
            persistence_mode = CorePersistenceMode.EPHEMERAL
        try:
            await OperationEngine(EphemeralStore()).execute(token, _intent("ephemeral", "ephemeral"), FakeProvider(store, ProviderDispatchCertainty.SUCCESS))
            assert False, "ephemeral external dispatch must fail closed"
        except CoreStateError as error:
            assert error.code == "DURABLE_OPERATION_REQUIRED"
    asyncio.run(run())


def test_operation_engine_rejects_real_provider_capability_mismatch_before_effect(tmp_path) -> None:
    class EffectProvider(FakeProvider):
        def __init__(self, store) -> None:
            super().__init__(store, ProviderDispatchCertainty.SUCCESS, CapabilityId(value="fake.audio"))
            self.effects = 0

        async def dispatch(self, intent):
            self.effects += 1
            return await super().dispatch(intent)

    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "capability-mismatch.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        provider = EffectProvider(store)
        result = await OperationEngine(store).execute(token, _intent("capability-mismatch", "capability-mismatch"), provider)
        durable = await store.get_operation(OperationId(value="capability-mismatch"))
        assert result.state == DurableOperationState.NOT_DISPATCHED
        assert result.not_dispatched_reason == NotDispatchedReason.CAPABILITY_UNAVAILABLE
        assert durable.state == DurableOperationState.NOT_DISPATCHED
        assert durable.not_dispatched_reason == NotDispatchedReason.CAPABILITY_UNAVAILABLE
        assert provider.calls == 0 and provider.effects == 0
    asyncio.run(run())


def test_operation_projection_rebuild_removes_facts_less_bogus_row(tmp_path) -> None:
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "operations-rebuild-facts-only.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        intent = _intent("real-operation", "real-fingerprint")
        committed = await store.commit_operation_intent(token, intent)
        assert committed.value is not None
        attempted = await store.mark_dispatch_attempted(token, intent.operation.operation_id, committed.value.revision)
        assert attempted.value is not None
        store._connection.execute("INSERT INTO operations(operation_id, fingerprint, payload) VALUES (?, ?, ?)", ("bogus-operation", "bogus-fingerprint", "{}"))
        store._connection.execute("UPDATE operations SET payload = ? WHERE operation_id = ?", ("{}", "real-operation"))
        await store.rebuild_projections(token)
        real = await store.get_operation(intent.operation.operation_id)
        assert real.state == DurableOperationState.DISPATCH_ATTEMPTED and real.revision == attempted.value.revision
        assert store._connection.execute("SELECT 1 FROM operations WHERE operation_id = ?", ("bogus-operation",)).fetchone() is None
    asyncio.run(run())


def test_idempotent_unknown_retries_same_operation_without_second_effect(tmp_path) -> None:
    class IdempotentUnknownProvider(FakeProvider):
        def __init__(self, store) -> None:
            super().__init__(store, ProviderDispatchCertainty.OUTCOME_UNKNOWN)
            self.effects = set()

        async def dispatch(self, intent):
            self.calls += 1
            self.effects.add(intent.operation.operation_id.value)
            return ProviderDispatchResult(certainty=ProviderDispatchCertainty.OUTCOME_UNKNOWN)

    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "retry.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        provider = IdempotentUnknownProvider(store)
        engine = OperationEngine(store)
        intent = _intent("idempotent-unknown", "fingerprint", IdempotencyKind.KEYED)
        assert (await engine.execute(token, intent, provider)).state == DurableOperationState.OUTCOME_UNKNOWN
        assert (await engine.execute(token, intent, provider)).state == DurableOperationState.OUTCOME_UNKNOWN
        assert provider.calls == 2 and provider.effects == {"idempotent-unknown"}
    asyncio.run(run())


def test_success_outcome_ack_loss_reconciles_durable_terminal_without_redrive(tmp_path) -> None:
    class AckLossProvider(FakeProvider):
        async def dispatch(self, intent):
            self.calls += 1
            self.store.simulate_semantic_outcome_unknown_once()
            return ProviderDispatchResult(certainty=ProviderDispatchCertainty.SUCCESS)

    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "ack-loss.db")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        provider = AckLossProvider(store, ProviderDispatchCertainty.SUCCESS)
        engine = OperationEngine(store)
        intent = _intent("ack-loss", "ack")
        try:
            await engine.execute(token, intent, provider)
            assert False, "unknown outcome commit acknowledgement must fail closed"
        except CoreStateError as error:
            assert error.code == "OPERATION_STATE_NOT_COMMITTED"
        assert (await store.get_operation(intent.operation.operation_id)).state == DurableOperationState.SUCCEEDED
        assert (await engine.execute(token, intent, provider)).state == DurableOperationState.SUCCEEDED
        assert provider.calls == 1
    asyncio.run(run())


def test_terminal_operations_do_not_redrive_after_restart(tmp_path) -> None:
    async def run() -> None:
        path = tmp_path / "terminal-restart.db"
        store = SQLiteCoreStateStore(path)
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        provider = FakeProvider(store, ProviderDispatchCertainty.SUCCESS)
        engine = OperationEngine(store)
        intent = _intent("terminal", "terminal")
        assert (await engine.execute(token, intent, provider)).state == DurableOperationState.SUCCEEDED
        await store.release_authority(token)
        restarted = SQLiteCoreStateStore(path)
        token2 = await restarted.acquire_authority(CoreInstanceId(value="restart"))
        assert (await OperationEngine(restarted).execute(token2, intent, provider)).state == DurableOperationState.SUCCEEDED
        assert provider.calls == 1
    asyncio.run(run())


def test_failed_and_non_idempotent_unknown_restart_never_redrive(tmp_path) -> None:
    async def run() -> None:
        path = tmp_path / "no-redrive.db"
        store = SQLiteCoreStateStore(path); token = await store.acquire_authority(CoreInstanceId(value="core")); engine = OperationEngine(store)
        failed_provider = FakeProvider(store, ProviderDispatchCertainty.DEFINITE_FAILURE)
        failed_intent = _intent("failed-restart", "failed")
        assert (await engine.execute(token, failed_intent, failed_provider)).state == DurableOperationState.FAILED
        unknown_provider = FakeProvider(store, ProviderDispatchCertainty.OUTCOME_UNKNOWN)
        unknown_intent = _intent("unknown-restart", "unknown", IdempotencyKind.NONE)
        assert (await engine.execute(token, unknown_intent, unknown_provider)).state == DurableOperationState.OUTCOME_UNKNOWN
        await store.release_authority(token); restarted = SQLiteCoreStateStore(path); token2 = await restarted.acquire_authority(CoreInstanceId(value="restart")); restarted_engine = OperationEngine(restarted)
        assert (await restarted_engine.execute(token2, failed_intent, failed_provider)).state == DurableOperationState.FAILED
        assert (await restarted_engine.execute(token2, unknown_intent, unknown_provider)).state == DurableOperationState.OUTCOME_UNKNOWN
        assert failed_provider.calls == 1 and unknown_provider.calls == 1
    asyncio.run(run())


def test_bad_provider_effect_raise_and_malformed_fail_closed(tmp_path) -> None:
    class EffectThenRaise(FakeProvider):
        async def dispatch(self, intent):
            self.calls += 1
            raise RuntimeError("effect may already have happened")
    class Malformed(FakeProvider):
        async def dispatch(self, intent):
            self.calls += 1
            return object()
    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "bad-provider.db"); token = await store.acquire_authority(CoreInstanceId(value="core")); engine = OperationEngine(store)
        raised = await engine.execute(token, _intent("raised", "raised", IdempotencyKind.NONE), EffectThenRaise(store, ProviderDispatchCertainty.SUCCESS))
        malformed = await engine.execute(token, _intent("malformed", "malformed"), Malformed(store, ProviderDispatchCertainty.SUCCESS))
        assert raised.state == DurableOperationState.OUTCOME_UNKNOWN and malformed.state == DurableOperationState.OUTCOME_UNKNOWN
    asyncio.run(run())
