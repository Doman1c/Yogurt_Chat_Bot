"""Durable intent-first operation orchestration; provider calls are never in DB TXs."""

from typing import Optional, Protocol

from yogurt_contracts.foundation.durability import CorePersistenceMode
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_contracts.operations.traits import IdempotencyKind
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken
from yogurt_contracts.foundation.identifiers import OperationId

from yogurt_core.operations.models import DurableOperationState, NotDispatchedReason, ProviderDispatchCertainty, ProviderDispatchResult, StoredOperation
from yogurt_core.operations.provider import OperationProviderPort
from yogurt_core.ports.state_store import CoreStateError, StateCommit


class DurableOperationStore(Protocol):
    persistence_mode: CorePersistenceMode

    async def commit_operation_intent(self, token: CoreAuthorityToken, intent: OperationIntentCommitted) -> StateCommit[StoredOperation]: ...
    async def mark_dispatch_attempted(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]: ...
    async def mark_succeeded(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]: ...
    async def mark_failed(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]: ...
    async def mark_outcome_unknown(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]: ...
    async def mark_not_dispatched(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int, reason: NotDispatchedReason) -> StateCommit[StoredOperation]: ...


class OperationEngine:
    def __init__(self, store: DurableOperationStore) -> None:
        self._store = store

    async def execute(self, token: CoreAuthorityToken, intent: OperationIntentCommitted, provider: OperationProviderPort, reject_reason: Optional[NotDispatchedReason] = None) -> StoredOperation:
        if self._store.persistence_mode != CorePersistenceMode.DURABLE:
            raise CoreStateError("DURABLE_OPERATION_REQUIRED")
        committed = await self._store.commit_operation_intent(token, intent)
        operation = self._confirmed(committed)
        if operation.state in {DurableOperationState.SUCCEEDED, DurableOperationState.FAILED, DurableOperationState.NOT_DISPATCHED}:
            return operation
        if operation.state == DurableOperationState.OUTCOME_UNKNOWN and intent.operation.traits.idempotency == IdempotencyKind.NONE:
            return operation
        if reject_reason is not None:
            return self._confirmed(await self._store.mark_not_dispatched(token, intent.operation.operation_id, operation.revision, reject_reason))
        if provider.capability_id != intent.operation.target_capability:
            return self._confirmed(await self._store.mark_not_dispatched(token, intent.operation.operation_id, operation.revision, NotDispatchedReason.CAPABILITY_UNAVAILABLE))
        attempted = self._confirmed(await self._store.mark_dispatch_attempted(token, intent.operation.operation_id, operation.revision))
        # This await is intentionally after the durable attempt commit.
        try:
            result = await provider.dispatch(intent)
        except Exception:
            return self._confirmed(await self._store.mark_outcome_unknown(token, intent.operation.operation_id, attempted.revision))
        if not isinstance(result, ProviderDispatchResult):
            return self._confirmed(await self._store.mark_outcome_unknown(token, intent.operation.operation_id, attempted.revision))
        if result.certainty == ProviderDispatchCertainty.SUCCESS:
            return self._confirmed(await self._store.mark_succeeded(token, intent.operation.operation_id, attempted.revision))
        if result.certainty == ProviderDispatchCertainty.DEFINITE_FAILURE:
            return self._confirmed(await self._store.mark_failed(token, intent.operation.operation_id, attempted.revision))
        return self._confirmed(await self._store.mark_outcome_unknown(token, intent.operation.operation_id, attempted.revision))

    @staticmethod
    def _confirmed(commit: StateCommit[StoredOperation]) -> StoredOperation:
        if commit.value is None or commit.certainty.value != "committed":
            raise CoreStateError("OPERATION_STATE_NOT_COMMITTED")
        return commit.value
