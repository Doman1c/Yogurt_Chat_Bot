"""W5 final cross-boundary evidence; only isolated stores and in-process HTTP."""

import asyncio

from tests.native.test_gateway import setup, close, post
from tests.core.test_legacy_state_downgrade import poison_legacy, SENTINEL
from tests.core.test_working_state import REF, admit
from yogurt_contracts.conversation.lifecycle import TurnState
from yogurt_contracts.foundation.identifiers import CoreInstanceId, ResponseId, TurnId
from yogurt_core.context.composition import ContextComposer
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.context.projection import ContextProjectionService
from yogurt_core.context.working_state import WorkingStateProjectionService
from yogurt_core_adapters.state_store_memory import InMemoryCoreStateStore
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore


def test_legacy_mutation_cannot_influence_any_core_or_native_output(tmp_path):
    async def run():
        core, store, gateway, profile, client, model, _ = await setup(tmp_path / "core.sqlite3")
        try:
            first = await post(client, {"client_message_id": "one", "text": "Core focus?", "surface_type": "native_ios"})
            await core.drain()
            second = await post(client, {"client_message_id": "two", "text": "continue", "surface_type": "native_ios",
                                         "conversation_id": first["conversation_id"]})
            await core.drain()
            ref = gateway.ref(profile, second["conversation_id"])
            tid = TurnId(value=second["turn_ref"])
            async def capture():
                turn = await store.get_turn(ref, tid)
                return (await WorkingStateProjectionService(store).project(ref, turn.turn_ref),
                        await ContextProjectionService(store).project(turn),
                        turn.snapshot.persona_snapshot,
                        await ContextComposer(store, ContextBudgetPolicy()).compose(turn),
                        (await client.get(second["event_stream_url"])).text,
                        await gateway.history(profile, second["conversation_id"]),
                        tuple(model.contexts))
            baseline = await capture()
            assert baseline[0].recent_focus and baseline[3].working_state
            assert "answer" in baseline[4] and "response.completed" in baseline[4]
            poison_legacy(tmp_path / "legacy.sqlite3")
            assert await capture() == baseline
            await store.rebuild_projections(core._require_token())
            assert await capture() == baseline
            assert SENTINEL not in repr(baseline)
        finally:
            await close(core, store, client)
    asyncio.run(run())


def test_sqlite_inmemory_equivalence_and_through_turn_rebuild(tmp_path):
    async def run(store, durable):
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        try:
            await store.create_conversation(token, REF)
            first = await admit(store, token, 1, "prior focus?")
            response = await store.commit_response_started(token, REF, first.turn_id, ResponseId(value="r1"))
            await store.finalize_response(token, response.response_id, response.revision)
            turn = await store.get_turn(REF, first.turn_id)
            await store.terminalize_turn(token, REF, first.turn_id, turn.revision, TurnState.COMPLETED)
            projector = WorkingStateProjectionService(store)
            frozen = await projector.project(REF, first)
            second = await admit(store, token, 2, "continue")
            turn = await store.get_turn(REF, second.turn_id)
            composer = ContextComposer(store, ContextBudgetPolicy())
            baseline = (await projector.project(REF, second), await composer.compose(turn))
            assert await projector.project(REF, first) == frozen
            if durable:
                await store.rebuild_projections(token)
                assert (await projector.project(REF, second), await composer.compose(turn)) == baseline
                assert await projector.project(REF, first) == frozen
            return baseline
        finally:
            await store.release_authority(token)
            if durable:
                await store.close()
    assert asyncio.run(run(InMemoryCoreStateStore(), False)) == asyncio.run(
        run(SQLiteCoreStateStore(tmp_path / "equivalent.sqlite3"), True))
