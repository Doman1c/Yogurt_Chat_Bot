"""W1 deterministic cognition and frozen acceptance #74."""

import asyncio
import sqlite3

import pytest

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.lifecycle import TurnState
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin, TurnRef
from yogurt_contracts.conversation.working_state import WorkingStateLifecycle as Life
from yogurt_contracts.execution.persona import PersonaSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import (
    ConversationId, CoreInstanceId, ExternalPrincipalRef, IdentityResolutionRef,
    InputId, PersonaId, ResponseId, SegmentId, TurnId, UserId,
)
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.response.lifecycle import ResponseAbortReason
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.context.projection import ContextProjectionService
from yogurt_core.context.working_state import (
    WorkingStateDeriver, WorkingStateProjectionService, WorkingStateTurnSource,
)
from yogurt_core.ports.state_store import CoreStateError
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore


REF = ConversationRef(persona_id=PersonaId(value="p"), user_id=UserId(value="u"), conversation_id=ConversationId(value="c"))


def source(index, text, done=False):
    return WorkingStateTurnSource(TurnRef(conversation_id=REF.conversation_id, turn_id=TurnId(value="t" + str(index))), text, done)


def derive(*sources):
    return WorkingStateDeriver().derive(REF, sources[-1].turn_ref if sources else None, sources)


def test_empty_normalization_focus_and_unicode():
    empty = derive()
    assert empty.revision == 0 and empty.derived_through_turn is None
    assert empty.active_threads == empty.recent_focus == empty.unresolved_items == ()
    a = derive(source(1, "  中文？\r\n cafe\u0301   测试\t"))
    b = derive(source(1, "中文？\n café 测试"))
    assert a == b
    assert a.recent_focus[0].value == "中文？ café 测试"
    assert a.unresolved_items[0].lifecycle == Life.ACTIVE
    assert a.revision == 2
    resolved = derive(source(1, "中文？\n café 测试", True))
    assert resolved.unresolved_items[0].entry_id == a.unresolved_items[0].entry_id
    assert resolved.unresolved_items[0].lifecycle == Life.RESOLVED
    assert resolved.revision == 3


def test_bounds_order_stable_threads_and_turn_distance_expiry():
    first = derive(source(1, "same"))
    repeated = derive(*(source(i, "same") for i in range(1, 12)))
    assert repeated.active_threads[0].entry_id == first.active_threads[0].entry_id
    assert repeated.active_threads[0].source_turn_refs == tuple(source(i, "").turn_ref for i in (9, 10, 11))
    assert len(repeated.recent_focus) == 3
    expired = derive(source(1, "same"), *(source(i, "new") for i in range(2, 6)))
    assert expired.active_threads[-1].lifecycle == Life.EXPIRED
    many = derive(*(source(i, str(i) + "?" + "中" * 200) for i in range(1, 30)))
    assert (len(many.active_threads), len(many.recent_focus), len(many.unresolved_items)) == (5, 3, 5)
    assert many.recent_focus[0].source_turn_refs == (source(29, "").turn_ref,)
    assert all(len(e.value) == 160 for e in many.active_threads + many.recent_focus + many.unresolved_items)
    assert len(derive(source(1, "x" * 160 + "a"), source(2, "x" * 160 + "b")).active_threads) == 2


def test_version_and_boundary_fail_closed():
    with pytest.raises(ValueError, match="UNSUPPORTED_WORKING_STATE_DERIVATION_VERSION"):
        WorkingStateDeriver("working-state-v2")
    with pytest.raises(ValueError, match="BOUNDARY"):
        WorkingStateDeriver().derive(REF, source(1, "").turn_ref, (source(2, "wrong"),))
    with pytest.raises(ValueError, match="BOUNDARY"):
        derive(source(1, "a"), source(1, "b"))


async def admit(store, token, index, text):
    origin = TurnOrigin(origin_surface="test", external_principal_ref=ExternalPrincipalRef(value="external"), identity_resolution_ref=IdentityResolutionRef(value="identity"))
    turn_input = TurnInput(input_id=InputId(value="input" + str(index)), parts=(TextInputPart(text=text),), origin=origin)
    persona = PersonaSnapshot(persona_id=REF.persona_id, persona_revision="1", soul_revision="1", identity_revision="1", relationship_context_revision="1", model_context_material="fixed persona")
    snapshot = TurnSnapshot(identity_revision="1", core_config_revision="1", turn_origin=origin, conversation_context_revision="1", principal_binding_reference="binding", persona_snapshot=persona)
    turn_ref = source(index, "").turn_ref
    await store.try_admit_turn(token, REF, turn_ref.turn_id, turn_fingerprint(turn_input, "binding"), snapshot, turn_input)
    return turn_ref


def test_frozen_74_restart_fact_rebuild_and_through_turn_freeze(tmp_path):
    async def run():
        path = tmp_path / "w1.sqlite3"
        store = SQLiteCoreStateStore(path)
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        await store.create_conversation(token, REF)
        projection = WorkingStateProjectionService(store)
        assert (await projection.project(REF, None)) == derive()
        turn_ref = await admit(store, token, 1, "Where are we？")
        pending = await projection.project(REF, turn_ref)
        assert pending.unresolved_items[0].lifecycle == Life.ACTIVE
        response = await store.commit_response_started(token, REF, turn_ref.turn_id, ResponseId(value="response"))
        response = await store.commit_segment(token, response.response_id, response.revision, AgentOutputSegmentCommitted(response_id=response.response_id, segment_id=SegmentId(value="segment"), sequence=1, text="committed, not yet finalized", language="en"))
        assert await projection.project(REF, turn_ref) == pending
        await store.finalize_response(token, response.response_id, response.revision)
        resolved = await projection.project(REF, turn_ref)
        assert resolved.unresolved_items[0].lifecycle == Life.RESOLVED
        turn = await store.get_turn(REF, turn_ref.turn_id)
        await store.terminalize_turn(token, REF, turn_ref.turn_id, turn.revision, TurnState.COMPLETED)
        next_ref = await admit(store, token, 2, "Next question?")
        assert await projection.project(REF, turn_ref) == resolved
        with sqlite3.connect(path) as connection:
            fact_count = connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        before = await projection.project(REF, next_ref)
        next_turn = await store.get_turn(REF, next_ref.turn_id)
        history_before = await ContextProjectionService(store).project(next_turn)
        model_before = ContextBudgetPolicy().compose(history_before, next_turn)
        assert await projection.project(REF, next_ref) == before
        history_after = await ContextProjectionService(store).project(next_turn)
        assert history_before == history_after
        assert model_before == ContextBudgetPolicy().compose(history_after, next_turn)
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == fact_count
            assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%working_state%'").fetchall()
        with pytest.raises(CoreStateError, match="BOUNDARY_REQUIRED"):
            await projection.project(REF, None)
        with pytest.raises(CoreStateError, match="BOUNDARY_NOT_FOUND"):
            await projection.project(REF, source(99, "").turn_ref)
        with pytest.raises(CoreStateError, match="BOUNDARY_MISMATCH"):
            await projection.project(REF, next_ref.model_copy(update={"conversation_id": ConversationId(value="other")}))
        await store.release_authority(token)
        await store.close()
        restarted = SQLiteCoreStateStore(path)
        token = await restarted.acquire_authority(CoreInstanceId(value="restarted"))
        again = WorkingStateProjectionService(restarted)
        assert await again.project(REF, next_ref) == before
        # #74: rebuild deletes relational projections, replays authoritative facts.
        await restarted.rebuild_projections(token)
        assert await again.project(REF, next_ref) == before
        assert await again.project(REF, turn_ref) == resolved
        assert await ContextProjectionService(restarted).project(next_turn) == history_before
        response = await restarted.commit_response_started(token, REF, next_ref.turn_id, ResponseId(value="next-response"))
        await restarted.finalize_response(token, response.response_id, response.revision)
        assert await again.project(REF, turn_ref) == resolved
        assert (await again.project(REF, next_ref)).unresolved_items[0].lifecycle == Life.RESOLVED
        await restarted.release_authority(token)
        await restarted.close()
    asyncio.run(run())


def test_uncommitted_input_and_other_conversation_do_not_enter_projection(tmp_path):
    async def run():
        store = SQLiteCoreStateStore(tmp_path / "certainty.sqlite3")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        await store.create_conversation(token, REF)
        ref = await admit(store, token, 1, "Only committed input")
        projection = WorkingStateProjectionService(store)
        before = await projection.project(REF, ref)
        other = REF.model_copy(update={"user_id": UserId(value="another-user")})
        await store.create_conversation(token, other)
        turn = await store.get_turn(REF, ref.turn_id)
        store.simulate_semantic_not_committed_once()
        rejected = await store.try_admit_turn_commit(token, other, TurnId(value="uncommitted"),
                                                    turn_fingerprint(turn.turn_input, "binding"), turn.snapshot, turn.turn_input)
        assert rejected.value is None
        assert await projection.project(other, None) == WorkingStateDeriver().derive(other, None, ())
        await store.try_admit_turn(token, other, ref.turn_id, turn_fingerprint(turn.turn_input, "binding"), turn.snapshot, turn.turn_input)
        assert await projection.project(REF, ref) == before
        await store.release_authority(token)
        await store.close()
    asyncio.run(run())


def test_aborted_response_does_not_resolve_question(tmp_path):
    async def run():
        store = SQLiteCoreStateStore(tmp_path / "abort.sqlite3")
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        await store.create_conversation(token, REF)
        ref = await admit(store, token, 1, "Pending?")
        projection = WorkingStateProjectionService(store)
        before = await projection.project(REF, ref)
        response = await store.commit_response_started(token, REF, ref.turn_id, ResponseId(value="aborted"))
        await store.abort_response(token, response.response_id, response.revision, ResponseAbortReason.CANCELLED)
        assert await projection.project(REF, ref) == before
        await store.release_authority(token)
        await store.close()
    asyncio.run(run())
