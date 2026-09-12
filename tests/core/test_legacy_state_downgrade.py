"""W4: real isolated legacy mutations have no authority over Core cognition."""

import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

from integrations.working_state_shadow.legacy import _factory
from tests.core.test_ephemeral_core import _core, _command
from tests.core.test_working_state import REF, admit
from yogurt_contracts.conversation.lifecycle import TurnState
from yogurt_contracts.foundation.identifiers import CoreInstanceId, ResponseId
from yogurt_core.context.composition import ContextComposer
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.context.working_state import WorkingStateProjectionService
from yogurt_core.model.events import VisibleTextDelta
from yogurt_core_adapters.model_fake import FakeModelAdapter
from yogurt_core_adapters.state_store_memory import InMemoryCoreStateStore
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SENTINEL = "LEGACY_ONLY_IGNORE_CORE_AUTHORITY"


def poison_legacy(path):
    legacy = _factory()(path)
    legacy.observe_user_turn("u", "c", "开始编写 V4 代码，然后测试", now=NOW)
    # Deliberately conflicting structural data, in a test-owned DB only.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE conversation_states SET current_topic=?, open_threads_json=?",
                           (SENTINEL, '["' + SENTINEL + '"]'))
        row = connection.execute("SELECT current_topic, open_threads_json FROM conversation_states").fetchone()
    assert row[0] == SENTINEL and SENTINEL in row[1]
    return legacy


@pytest.mark.parametrize("durable", [False, True])
def test_legacy_mutation_cannot_change_core_state_or_composition(tmp_path, durable):
    async def run():
        store = SQLiteCoreStateStore(tmp_path / "core.sqlite3") if durable else InMemoryCoreStateStore()
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        try:
            await store.create_conversation(token, REF)
            first = await admit(store, token, 1, "Core prior focus?")
            response = await store.commit_response_started(token, REF, first.turn_id, ResponseId(value="response"))
            await store.finalize_response(token, response.response_id, response.revision)
            turn = await store.get_turn(REF, first.turn_id)
            await store.terminalize_turn(token, REF, first.turn_id, turn.revision, TurnState.COMPLETED)
            current = await admit(store, token, 2, "continue")
            turn = await store.get_turn(REF, current.turn_id)
            projector = WorkingStateProjectionService(store)
            composer = ContextComposer(store, ContextBudgetPolicy())
            state = await projector.project(REF, current)
            context = await composer.compose(turn)
            poison_legacy(tmp_path / "legacy.sqlite3")
            assert await projector.project(REF, current) == state
            assert await composer.compose(turn) == context
            assert context.working_state is not None
            assert "Core prior focus?" in context.working_state.material
            assert SENTINEL not in repr(context)
        finally:
            await store.release_authority(token)
            if durable:
                await store.close()
    asyncio.run(run())


def test_real_core_model_context_independent_of_divergent_legacy_databases(tmp_path):
    async def conversation():
        model = FakeModelAdapter(((VisibleTextDelta(text="answer"),), ()))
        core, _ = await _core(model=model)
        try:
            await core.submit_turn(_command(text="Core focus"))
            await core.drain()
            await core.submit_turn(_command("t2", "continue"))
            await core.drain()
            return tuple(model.contexts)
        finally:
            await core.stop()
    baseline = asyncio.run(conversation())
    poison_legacy(tmp_path / "divergent.sqlite3")
    assert asyncio.run(conversation()) == baseline


def test_legacy_compatibility_fields_remain_operational(tmp_path):
    legacy = _factory()(tmp_path / "compat.sqlite3")
    technical = legacy.observe_user_turn("u", "c", "开始编写 V4 代码，然后测试", now=NOW)
    assert technical.dialogue_act == "plan"
    assert technical.interaction_mode == "technical"
    assert technical.current_topic and technical.open_threads
    sad = legacy.observe_user_turn("u", "sad", "今天很难过", now=NOW)
    assert sad.user_mood == "sad"
    after = legacy.observe_assistant_turn("u", "c", "测试通过，下面是代码：\n```python\nprint('v4')\n```", now=NOW)
    assert after.last_response_pattern == "technical_code"
    assert after.recent_actions == ("V4.0 测试已通过",)
    assert after.state_version > technical.state_version
