"""R3-C isolated V5 HTTP conformance.  This suite never addresses port 17621."""

import asyncio
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SOCIAL_SRC = ROOT / "social_layer" / "src"
if str(SOCIAL_SRC) not in sys.path:
    sys.path.insert(0, str(SOCIAL_SRC))

from yogurt_social.memory_service.core import MemoryService, MemoryServiceConfig

from integrations.memory_v5 import (
    IsolatedMemoryV5Process, MemoryV5ObservationProvider, MemoryV5RecallAdapter,
    MemoryV5ShadowObservationFlow,
    MemoryV5ShadowEndpoint, MemoryV5ShadowRecallFlow, ShadowAuditStream,
    copy_isolated_memory_database,
)
from yogurt_contracts.capability.binding import CapabilityBinding
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.execution import CapabilityAuthorizationSnapshot, OperationAuthorizationSnapshot
from yogurt_contracts.foundation.durability import DurabilityLevel
from yogurt_contracts.foundation.identifiers import (
    BindingId, CapabilityId, ConversationId, CoreInstanceId, InputId, MemoryRecallQueryId,
    OperationId, PersonaId, RuntimeInstanceId, RuntimeSlotId, TurnId, UserId,
)
from yogurt_contracts.foundation.idempotency import RequestFingerprint
from yogurt_contracts.foundation.time import Deadline, UtcInstant
from yogurt_contracts.foundation.versioning import ContractVersion
from yogurt_contracts.identity.authority import AuthorityClaims, DelegatedAuthority
from yogurt_contracts.memory.causal import MemoryCausalResolution, MemoryObservationOperation, MemoryReconciliationProof, memory_observation_fingerprint
from yogurt_contracts.memory.observations import UserContentObservation
from yogurt_contracts.memory.recall import MEMORY_RECALL_CAPABILITY_ID, MemoryRecallBindingSnapshot, MemoryRecallQuery, RecallEligibility, memory_conversation_scope_ref
from yogurt_contracts.operations.intent import LogicalOperation, OperationIntentCommitted
from yogurt_contracts.operations.traits import CancellationSupport, EffectKind, IdempotencyKind, OperationTraits, ReversibilityKind
from yogurt_core.memory.observation_operations import MemoryObservationOperationCoordinator
from yogurt_core.memory.causal_lane import MemoryCausalLane
from yogurt_core.memory.recall_policy import MemoryContextAdmissionStatus, MemoryRecallAdmissionPolicy, MemoryRecallDispatcher
from yogurt_core.operations.engine import OperationEngine
from yogurt_core.ports.state_store import CoreStateError
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore


NOW = UtcInstant(value=datetime(2026, 8, 31, tzinfo=timezone.utc))
FUTURE = UtcInstant(value=NOW.value + timedelta(minutes=10))
CONVERSATION = ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="shadow-user"), conversation_id=ConversationId(value="shadow-conversation"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic(operation: str) -> MemoryObservationOperation:
    observation = UserContentObservation(operation_id=OperationId(value=operation), conversation_ref=CONVERSATION, turn_ref=TurnRef(conversation_id=CONVERSATION.conversation_id, turn_id=TurnId(value="turn-" + operation)), input_id=InputId(value="input-" + operation), content="gpu RTX 4080", observed_at=NOW)
    return MemoryObservationOperation(operation_id=observation.operation_id, observation=observation, scope_ref=memory_conversation_scope_ref(CONVERSATION), logical_provider_domain="memory-v5-shadow", observation_fingerprint=memory_observation_fingerprint(observation))


def _intent(operation: str, endpoint: MemoryV5ShadowEndpoint) -> OperationIntentCommitted:
    capability = CapabilityId(value="memory.observe")
    claims = AuthorityClaims(actor=RuntimeInstanceId(value="core"), subject=CONVERSATION.user_id, origin="core", audience=endpoint.runtime_instance_id, scope=capability, grant_revision="grant", valid_until=FUTURE, authority_reference="authority")
    logical = LogicalOperation(operation_id=OperationId(value=operation), request_fingerprint=_semantic(operation).observation_fingerprint, target_capability=capability, authority=DelegatedAuthority(claims=claims, operation_id=OperationId(value=operation)), deadline=Deadline(expires_at=FUTURE), traits=OperationTraits(effect_kind=EffectKind.WRITE, idempotency=IdempotencyKind.KEYED, reversibility=ReversibilityKind.IRREVERSIBLE, cancellation_support=CancellationSupport.BEST_EFFORT), created_at=NOW)
    binding = CapabilityBinding(binding_id=BindingId(value="observe-shadow-binding"), binding_revision="6", capability_id=capability, provider_slot_id=RuntimeSlotId(value="shadow-slot"), provider_instance_id=endpoint.runtime_instance_id, provider_generation=endpoint.runtime_generation, contract_version=ContractVersion(major=1, minor=0), authority_valid_until=FUTURE)
    return OperationIntentCommitted(operation=logical, provider_binding=binding, authorization_snapshot=OperationAuthorizationSnapshot(operation_id=logical.operation_id, claims=claims, checked_at=NOW, valid_until=FUTURE), required_durability=DurabilityLevel.DURABLY_PERSISTED, committed_at=NOW)


def _binding(endpoint: MemoryV5ShadowEndpoint) -> MemoryRecallBindingSnapshot:
    return MemoryRecallBindingSnapshot(binding_id=BindingId(value="recall-shadow-binding"), logical_provider_id=endpoint.logical_provider_domain, runtime_instance_id=endpoint.runtime_instance_id, runtime_generation=endpoint.runtime_generation, binding_revision="6", memory_domain_id="shadow-domain", capability_id=CapabilityId(value=MEMORY_RECALL_CAPABILITY_ID))


def _query(semantic: MemoryObservationOperation) -> MemoryRecallQuery:
    return MemoryRecallQuery(recall_query_id=MemoryRecallQueryId(value="query-" + semantic.operation_id.value), conversation_ref=CONVERSATION, turn_id=semantic.observation.turn_ref.turn_id, memory_conversation_scope_ref=semantic.scope_ref, normalized_user_message=semantic.observation.content, current_input_fingerprint=semantic.observation_fingerprint, max_items=3, token_budget=64, deadline=Deadline(expires_at=FUTURE), query_fingerprint=RequestFingerprint(value="query-fingerprint-" + semantic.operation_id.value))


def _authorization(binding: MemoryRecallBindingSnapshot) -> CapabilityAuthorizationSnapshot:
    claims = AuthorityClaims(actor=RuntimeInstanceId(value="core"), subject=CONVERSATION.user_id, origin="core", audience=binding.runtime_instance_id, scope=CapabilityId(value=MEMORY_RECALL_CAPABILITY_ID), grant_revision="grant", valid_until=FUTURE, authority_reference="authority")
    return CapabilityAuthorizationSnapshot(claims=claims, authorization_revision="shadow-auth", checked_at=NOW, valid_until=FUTURE)


@pytest.fixture
def shadow_v5(tmp_path):
    source_db = tmp_path / "protected-source.sqlite3"
    protected_audit = tmp_path / "protected-audit.jsonl"
    protected_audit.write_text("protected-audit\n", encoding="utf-8")
    seed = MemoryService(MemoryServiceConfig(source_db))
    seed.memory.add_fact("persona:shadow-user", "gpu", "RTX 4080")
    source_hash, audit_hash = _sha(source_db), _sha(protected_audit)
    shadow_db = copy_isolated_memory_database(source_db, tmp_path / "shadow" / "memory.sqlite3")
    audit = ShadowAuditStream(tmp_path / "shadow" / "r3c-audit.jsonl")
    endpoint = MemoryV5ShadowEndpoint(base_url="http://127.0.0.1:" + str(IsolatedMemoryV5Process.unused_local_port()), logical_provider_domain="memory-v5-shadow", runtime_instance_id=RuntimeInstanceId(value="shadow-runtime"), runtime_generation=6, audit=audit)
    process = IsolatedMemoryV5Process(endpoint, shadow_db, SOCIAL_SRC)
    process.start()
    try:
        yield endpoint, shadow_db, audit, source_db, protected_audit
    finally:
        process.stop()
        assert _sha(source_db) == source_hash
        assert _sha(protected_audit) == audit_hash


def test_real_isolated_v5_observe_restart_idempotency_and_audit(shadow_v5, tmp_path) -> None:
    endpoint, shadow_db, audit, source_db, protected_audit = shadow_v5

    async def run() -> None:
        semantic, intent = _semantic("observe"), _intent("observe", endpoint)
        store = SQLiteCoreStateStore(tmp_path / "core.sqlite3")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        provider = MemoryV5ObservationProvider(endpoint, semantic)
        coordinator = MemoryObservationOperationCoordinator(store, OperationEngine(store))
        first = await MemoryV5ShadowObservationFlow(coordinator, store, endpoint.audit).execute(token, semantic, intent, provider)
        lane = await store.get_memory_causal_lane(semantic)
        assert first.durable_operation.state.value == "succeeded"
        assert lane.operations[0].resolution == MemoryCausalResolution.APPLIED
        await store.release_authority(token)
        await store.close()
        restarted = SQLiteCoreStateStore(tmp_path / "core.sqlite3")
        token = await restarted.acquire_authority(CoreInstanceId(value="restarted"))
        repeated = await MemoryObservationOperationCoordinator(restarted, OperationEngine(restarted)).execute(token, semantic, intent, provider)
        assert repeated.durable_operation.state.value == "succeeded"
        await restarted.release_authority(token)

    asyncio.run(run())
    with sqlite3.connect(shadow_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_observe_requests WHERE operation = 'observe_user'").fetchone()[0] == 1
    assert source_db != shadow_db and protected_audit.read_text(encoding="utf-8") == "protected-audit\n"
    assert "gpu=RTX 4080" not in audit.path.read_text(encoding="utf-8")


def test_real_v5_recall_requires_causal_apply_and_filters_current_content(shadow_v5, tmp_path) -> None:
    endpoint, _, audit, _, _ = shadow_v5

    async def run() -> None:
        semantic, intent = _semantic("recall"), _intent("recall", endpoint)
        store = SQLiteCoreStateStore(tmp_path / "core.sqlite3")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        await MemoryObservationOperationCoordinator(store, OperationEngine(store)).execute(token, semantic, intent, MemoryV5ObservationProvider(endpoint, semantic))
        binding, query = _binding(endpoint), _query(semantic)
        flow = MemoryV5ShadowRecallFlow(MemoryRecallDispatcher(MemoryV5RecallAdapter(endpoint, binding)), audit)
        result = await flow.recall(query, binding, _authorization(binding), NOW, await store.get_memory_causal_lane(semantic), semantic)
        assert result.eligibility == RecallEligibility.ELIGIBLE and result.evidence is not None
        current_candidate = result.evidence.candidates[0].model_copy(update={"source_turn_id": query.turn_id})
        current_evidence = result.evidence.model_copy(update={"candidates": (current_candidate,)})
        admission = MemoryRecallAdmissionPolicy().admit(query, current_evidence, binding, _authorization(binding), NOW)
        assert admission.status == MemoryContextAdmissionStatus.FILTERED_EMPTY
        assert admission.reason.value == "self_recall_filtered_empty"
        flow.record_admission(query, binding, result, admission)
        pending = _semantic("pending")
        await store.commit_memory_observation_intent(token, pending, _intent("pending", endpoint))
        blocked = await flow.recall(_query(pending), binding, _authorization(binding), NOW, await store.get_memory_causal_lane(pending), pending)
        assert blocked.eligibility == RecallEligibility.PRIOR_CAUSAL_OBSERVATION_PENDING
        incomplete_lane = MemoryCausalLane.empty(pending)
        incomplete_lane.allocate(pending)
        incomplete_lane.resolve(pending.operation_id, MemoryCausalResolution.RESOLVED_NOT_APPLIED)
        incomplete = await flow.recall(_query(pending), binding, _authorization(binding), NOW, incomplete_lane.snapshot(), pending)
        assert incomplete.eligibility == RecallEligibility.HISTORY_INCOMPLETE
        await store.release_authority(token)

    asyncio.run(run())
    events = [json.loads(line) for line in audit.path.read_text(encoding="utf-8").splitlines()]
    assert any(item["event_type"] == "memory_recall" and item["recall_eligibility"] == "eligible" for item in events)
    assert any(item["recall_eligibility"] == "prior_causal_observation_pending" for item in events)
    assert any(item["self_recall_filtered_count"] == 1 for item in events)


def test_transport_uncertainty_blocks_lane_and_proof_rejects_cross_domain(tmp_path) -> None:
    audit = ShadowAuditStream(tmp_path / "audit.jsonl")
    endpoint = MemoryV5ShadowEndpoint(base_url="http://127.0.0.1:" + str(IsolatedMemoryV5Process.unused_local_port()), logical_provider_domain="memory-v5-shadow", runtime_instance_id=RuntimeInstanceId(value="shadow-runtime"), runtime_generation=6, audit=audit)

    async def run() -> None:
        store = SQLiteCoreStateStore(tmp_path / "core.sqlite3")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        first, later = _semantic("uncertain"), _semantic("later")
        coordinator = MemoryObservationOperationCoordinator(store, OperationEngine(store))
        unknown = await coordinator.execute(token, first, _intent("uncertain", endpoint), MemoryV5ObservationProvider(endpoint, first, timeout_seconds=0.05))
        blocked = await coordinator.execute(token, later, _intent("later", endpoint), MemoryV5ObservationProvider(endpoint, later, timeout_seconds=0.05))
        assert unknown.durable_operation.state.value == "outcome_unknown" and blocked.durable_operation.state.value == "intent_committed"
        proof = MemoryReconciliationProof(operation_id=first.operation_id, logical_provider_domain="memory-v5-shadow", runtime_instance_id=RuntimeInstanceId(value="shadow-restarted"), runtime_generation=7, binding_id=BindingId(value="proof-binding"), resolution_reference="isolated-evidence")
        with pytest.raises(CoreStateError, match="PROOF_MISMATCH"):
            await store.reconcile_memory_observation(token, first, MemoryCausalResolution.APPLIED, proof.model_copy(update={"logical_provider_domain": "other-domain"}))
        await store.reconcile_memory_observation(token, first, MemoryCausalResolution.APPLIED, proof)
        await store.release_authority(token)

    asyncio.run(run())
