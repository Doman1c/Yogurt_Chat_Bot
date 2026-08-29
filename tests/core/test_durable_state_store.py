import asyncio
import functools
import sqlite3
from datetime import datetime, timezone

import pytest

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.lifecycle import TurnState
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.facts import CommitCertainty, FactEnvelope
from yogurt_contracts.foundation.identifiers import (
    ConversationId,
    CoreInstanceId,
    ExecutionAttemptId,
    ExternalPrincipalRef,
    FactId,
    IdentityResolutionRef,
    InputId,
    PersonaId,
    ResponseId,
    SegmentId,
    TurnId,
    UserId,
)
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.response.lifecycle import ResponseAbortReason, ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.execution import ExecutionAttemptSnapshot

from yogurt_core.ports.state_store import CoreStateError
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore


def _async_test(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapped


def _ref() -> ConversationRef:
    return ConversationRef(persona_id=PersonaId(value="persona"), user_id=UserId(value="user"), conversation_id=ConversationId(value="conversation"))


def _input() -> TurnInput:
    origin = TurnOrigin(origin_surface="test", external_principal_ref=ExternalPrincipalRef(value="external"), identity_resolution_ref=IdentityResolutionRef(value="resolved"))
    return TurnInput(input_id=InputId(value="input"), parts=(TextInputPart(text="hello"),), origin=origin)


def _snapshot() -> TurnSnapshot:
    return TurnSnapshot(identity_revision="identity", core_config_revision="config", turn_origin=_input().origin, conversation_context_revision="context", principal_binding_reference="binding")


@_async_test
async def test_sqlite_store_persists_atomic_turn_and_terminal_claim_release(tmp_path) -> None:
    path = tmp_path / "core.db"
    store = SQLiteCoreStateStore(path)
    token = await store.acquire_authority(CoreInstanceId(value="core-a"))
    turn_id = TurnId(value="turn-a")
    await store.create_conversation(token, _ref())
    admitted = await store.try_admit_turn(token, _ref(), turn_id, turn_fingerprint(_input(), "binding"), _snapshot(), _input())
    assert admitted.status.value == "newly_admitted"
    assert (await store.active_claim(_ref())) == turn_id
    response = await store.commit_response_started(token, _ref(), turn_id, ResponseId(value="response-a"))
    response = await store.add_execution_attempt(token, response.response_id, ExecutionAttemptSnapshot(execution_attempt_id=ExecutionAttemptId(value="attempt-a"), response_id=response.response_id, binding_snapshots=(), started_at=UtcInstant(value=datetime.now(timezone.utc))))
    response = await store.commit_segment(token, response.response_id, response.revision, AgentOutputSegmentCommitted(response_id=response.response_id, segment_id=SegmentId(value="segment-a"), sequence=1, text="hi", language="en"))
    response = await store.finalize_response(token, response.response_id, response.revision)
    turn = await store.get_turn(_ref(), turn_id)
    terminal = await store.terminalize_turn(token, _ref(), turn_id, turn.revision, TurnState.COMPLETED)
    assert terminal.state == TurnState.COMPLETED
    assert await store.active_claim(_ref()) is None
    await store.release_authority(token)
    await store.close()

    restarted = SQLiteCoreStateStore(path)
    token_b = await restarted.acquire_authority(CoreInstanceId(value="core-b"))
    assert (await restarted.get_turn(_ref(), turn_id)).state == TurnState.COMPLETED
    assert (await restarted.get_response(response.response_id)).segments[0].text == "hi"
    await restarted.rebuild_projections(token_b)
    assert (await restarted.get_turn(_ref(), turn_id)).state == TurnState.COMPLETED


@_async_test
async def test_sqlite_authority_fences_second_and_stale_core(tmp_path) -> None:
    path = tmp_path / "authority.db"
    first = SQLiteCoreStateStore(path)
    second = SQLiteCoreStateStore(path)
    token_a = await first.acquire_authority(CoreInstanceId(value="core-a"))
    with pytest.raises(CoreStateError, match="CORE_AUTHORITY_ALREADY_OWNED"):
        await second.acquire_authority(CoreInstanceId(value="core-b"))
    await first.release_authority(token_a)
    token_b = await second.acquire_authority(CoreInstanceId(value="core-b"))
    with pytest.raises(CoreStateError, match="STALE_CORE_AUTHORITY"):
        await first.create_conversation(token_a, _ref())
    await second.create_conversation(token_b, _ref())


@_async_test
async def test_sqlite_admission_is_atomic_under_concurrent_retries(tmp_path) -> None:
    store = SQLiteCoreStateStore(tmp_path / "admission.db")
    token = await store.acquire_authority(CoreInstanceId(value="core"))
    await store.create_conversation(token, _ref())
    turn_id = TurnId(value="turn")
    fingerprint = turn_fingerprint(_input(), "binding")
    results = await asyncio.gather(*[store.try_admit_turn(token, _ref(), turn_id, fingerprint, _snapshot(), _input()) for _ in range(8)])
    assert sum(result.status.value == "newly_admitted" for result in results) == 1
    assert {result.status.value for result in results} == {"newly_admitted", "same_logical_turn"}
    assert await store.active_claim(_ref()) == turn_id


@_async_test
async def test_stable_fact_id_reconciles_unknown_commit_and_rejects_conflict(tmp_path) -> None:
    store = SQLiteCoreStateStore(tmp_path / "facts.db")
    token = await store.acquire_authority(CoreInstanceId(value="core"))
    fact = FactEnvelope(fact_id=FactId(value="fact-stable"), fact_type="test", fact_schema_version=1, aggregate_id="aggregate", aggregate_revision=1, recorded_at=UtcInstant(value=datetime.now(timezone.utc)), payload={"value": "one"})
    store.simulate_outcome_unknown_once()
    result = await store.append_fact(token, fact)
    assert result.certainty == CommitCertainty.OUTCOME_UNKNOWN
    assert await store.reconcile_fact(fact.fact_id) == fact
    assert (await store.append_fact(token, fact)).certainty == CommitCertainty.COMMITTED
    with pytest.raises(CoreStateError, match="FACT_ID_CONFLICT"):
        await store.append_fact(token, fact.model_copy(update={"payload": {"value": "changed"}}))


@_async_test
async def test_projection_rebuild_upcasts_v0_and_incompatible_fact_stops_serving(tmp_path) -> None:
    path = tmp_path / "schema.db"
    store = SQLiteCoreStateStore(path)
    token = await store.acquire_authority(CoreInstanceId(value="core"))
    await store.release_authority(token)
    connection = sqlite3.connect(path)
    now = datetime.now(timezone.utc).isoformat()
    connection.execute("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?)", ("legacy", "legacy", 0, "legacy", 1, now, "{}"))
    connection.commit()
    connection.close()
    token = await store.acquire_authority(CoreInstanceId(value="core"))
    await store.rebuild_projections(token)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?)", ("unsupported", "unsupported", 2, "unsupported", 1, now, "{}"))
    connection.commit()
    connection.close()
    with pytest.raises(CoreStateError, match="INCOMPATIBLE_FACT_SCHEMA"):
        await store.rebuild_projections(token)
    assert not store.is_serving


@_async_test
async def test_corrupt_store_is_never_reset_or_serving(tmp_path) -> None:
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a sqlite database")
    store = SQLiteCoreStateStore(path)
    assert not store.is_serving
    with pytest.raises(CoreStateError, match="STORE_NOT_SERVING"):
        await store.acquire_authority(CoreInstanceId(value="core"))


@_async_test
async def test_semantic_commit_unknown_reuses_admission_segment_and_terminal_identities(tmp_path) -> None:
    store = SQLiteCoreStateStore(tmp_path / "certainty.db")
    token = await store.acquire_authority(CoreInstanceId(value="core"))
    await store.create_conversation(token, _ref())
    turn_id = TurnId(value="certainty-turn")
    fingerprint = turn_fingerprint(_input(), "binding")
    store.simulate_semantic_outcome_unknown_once()
    admission = await store.try_admit_turn_commit(token, _ref(), turn_id, fingerprint, _snapshot(), _input())
    assert admission.certainty == CommitCertainty.OUTCOME_UNKNOWN
    assert (await store.try_admit_turn_commit(token, _ref(), turn_id, fingerprint, _snapshot(), _input())).value.status.value == "same_logical_turn"
    response = await store.commit_response_started(token, _ref(), turn_id, ResponseId(value="certainty-response"))
    segment = AgentOutputSegmentCommitted(response_id=response.response_id, segment_id=SegmentId(value="certainty-segment"), sequence=1, text="once", language="en")
    store.simulate_semantic_outcome_unknown_once()
    committed = await store.commit_segment_commit(token, response.response_id, response.revision, segment)
    assert committed.certainty == CommitCertainty.OUTCOME_UNKNOWN
    assert (await store.commit_segment_commit(token, response.response_id, response.revision, segment)).value.segments == (segment,)
    turn = await store.get_turn(_ref(), turn_id)
    store.simulate_semantic_outcome_unknown_once()
    terminal = await store.terminalize_turn_commit(token, _ref(), turn_id, turn.revision, TurnState.CANCELLED)
    assert terminal.certainty == CommitCertainty.OUTCOME_UNKNOWN
    assert (await store.terminalize_turn_commit(token, _ref(), turn_id, turn.revision, TurnState.CANCELLED)).value.state == TurnState.CANCELLED
    assert await store.active_claim(_ref()) is None


@_async_test
async def test_recovery_closes_input_started_and_converges_terminal_response(tmp_path) -> None:
    path = tmp_path / "recovery.db"
    store = SQLiteCoreStateStore(path)
    token = await store.acquire_authority(CoreInstanceId(value="core-a"))
    await store.create_conversation(token, _ref())
    started_turn = TurnId(value="started")
    await store.try_admit_turn(token, _ref(), started_turn, turn_fingerprint(_input(), "binding"), _snapshot(), _input())
    await store.commit_response_started(token, _ref(), started_turn, ResponseId(value="response-started"))
    await store.release_authority(token)
    recovered = SQLiteCoreStateStore(path)
    token_b = await recovered.acquire_authority(CoreInstanceId(value="core-b"))
    await recovered.recover(token_b)
    started_response = await recovered.get_response(ResponseId(value="response-started"))
    assert started_response.state == ResponseState.ABORTED
    assert started_response.abort_reason == ResponseAbortReason.RECOVERY
    assert (await recovered.get_turn(_ref(), started_turn)).state == TurnState.ABORTED
    assert await recovered.active_claim(_ref()) is None
    await recovered.recover(token_b)
    assert (await recovered.get_turn(_ref(), started_turn)).state == TurnState.ABORTED
