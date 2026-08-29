"""Focused R2-A reference-runtime conformance and race tests."""

import asyncio
from datetime import datetime, timezone

import pytest

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin
from yogurt_contracts.execution.persona import PersonaSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import (
    CoreInstanceId,
    ConversationId,
    ExternalPrincipalRef,
    IdentityResolutionRef,
    InputId,
    PersonaId,
    ResponseId,
    SegmentId,
    TurnId,
    UserId,
)
from yogurt_contracts.identity.principal import ExternalPrincipal
from yogurt_contracts.identity.resolution import IdentityResolution
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_core.application.commands import CallerContext, GetResponseCommand, GetTurnCommand, SubmitTurnCommand, WatchResponseCommand
from yogurt_core.application.core import YogurtCore
from yogurt_core.application.results import TurnAdmissionStatus
from yogurt_core.model.events import AttemptTerminal, AttemptTerminalKind, VisibleTextDelta
from yogurt_core.model.execution import ExecutionAttemptSnapshot
from yogurt_core.response.stream import ResponseEventStream
from yogurt_core_adapters.identity_fake import FakeIdentityResolver
from yogurt_core_adapters.model_fake import FakeModelAdapter
from yogurt_core_adapters.persona_fake import FakePersonaSnapshotPort
from yogurt_core_adapters.state_store_memory import CoreStateError, InMemoryCoreStateStore


def _ref(user: str = "u1", conversation: str = "c1") -> ConversationRef:
    return ConversationRef(persona_id=PersonaId(value="yogurt"), user_id=UserId(value=user), conversation_id=ConversationId(value=conversation))


def _origin() -> TurnOrigin:
    return TurnOrigin(origin_surface="test", external_principal_ref=ExternalPrincipalRef(value="external-u1"), identity_resolution_ref=IdentityResolutionRef(value="res-u1"))


def _persona(revision: str = "p1") -> PersonaSnapshot:
    return PersonaSnapshot(persona_id=PersonaId(value="yogurt"), persona_revision=revision, soul_revision="s1", identity_revision="i1", relationship_context_revision="r1", model_context_material="persona-" + revision)


def _command(turn: str = "t1", text: str = "hello", user: str = "u1", conversation: str = "c1") -> SubmitTurnCommand:
    origin = _origin()
    turn_input = TurnInput(input_id=InputId(value="input-" + turn), parts=(TextInputPart(text=text),), origin=origin)
    return SubmitTurnCommand(
        caller_context=CallerContext(logical_user_id=UserId(value=user)),
        external_principal=ExternalPrincipal(reference=origin.external_principal_ref, authenticated_surface="test"),
        conversation_ref=_ref(user, conversation), turn_id=TurnId(value=turn), fingerprint=turn_fingerprint(turn_input, "binding"), input=turn_input,
        core_config_revision="config-1", conversation_context_revision="context-1", principal_binding_reference="binding",
    )


async def _core(model: FakeModelAdapter):
    store = InMemoryCoreStateStore()
    identity = FakeIdentityResolver({"external-u1": IdentityResolution(reference=IdentityResolutionRef(value="res-u1"), logical_user=UserId(value="u1"), identity_revision="identity-1", source_hash="source")})
    personas = FakePersonaSnapshotPort({"yogurt": _persona()})
    core = YogurtCore(store, identity, personas, model, CoreInstanceId(value="core-1"))
    await core.start()
    await core.create_conversation(_ref())
    return core, store, personas


def test_admission_idempotency_optional_response_and_frozen_persona():
    async def scenario():
        model = FakeModelAdapter(((VisibleTextDelta(text="hi", language="en"), AttemptTerminal(kind=AttemptTerminalKind.SUCCEEDED)),))
        core, _, personas = await _core(model)
        command = _command()
        first = await core.submit_turn(command)
        duplicate_before_start = await core.submit_turn(command)
        assert first.status == TurnAdmissionStatus.NEWLY_ADMITTED
        assert duplicate_before_start.status == TurnAdmissionStatus.SAME_LOGICAL_TURN
        assert duplicate_before_start.response_id is None
        personas.replace(_persona("p2"))
        await core.drain()
        duplicate_after_start = await core.submit_turn(command)
        assert duplicate_after_start.response_id is not None
        assert model.contexts[0].persona_material == "persona-p1"
    asyncio.run(scenario())


def test_cross_user_conflict_and_busy_reject_without_extra_admission():
    async def scenario():
        core, store, _ = await _core(FakeModelAdapter(((),)))
        first = await core.submit_turn(_command())
        assert first.status == TurnAdmissionStatus.NEWLY_ADMITTED
        assert (await core.submit_turn(_command(text="changed"))).status == TurnAdmissionStatus.TURN_ID_CONFLICT
        assert (await core.submit_turn(_command(turn="t2"))).status == TurnAdmissionStatus.CONVERSATION_BUSY
        assert (await core.submit_turn(_command(turn="other", user="u2"))).status == TurnAdmissionStatus.REJECTED
        assert await store.active_claim(_ref()) == TurnId(value="t1")
    asyncio.run(scenario())


def test_racing_turns_get_one_atomic_claim_and_same_turn_id_is_scoped_to_conversation():
    async def scenario():
        core, store, _ = await _core(FakeModelAdapter(((), ())))
        await core.create_conversation(_ref(conversation="c2"))
        raced = await asyncio.gather(core.submit_turn(_command(turn="a")), core.submit_turn(_command(turn="b")))
        assert sorted(result.status for result in raced) == [TurnAdmissionStatus.CONVERSATION_BUSY, TurnAdmissionStatus.NEWLY_ADMITTED]
        other = await core.submit_turn(_command(turn="a", conversation="c2"))
        assert other.status == TurnAdmissionStatus.NEWLY_ADMITTED
        assert (await store.get_turn(_ref(), TurnId(value="a"))).turn_ref.turn_id == TurnId(value="a")
        assert (await store.get_turn(_ref(conversation="c2"), TurnId(value="a"))).conversation_ref.conversation_id == ConversationId(value="c2")
    asyncio.run(scenario())


def test_stale_authority_cancel_winner_and_response_stream_is_narrow():
    async def scenario():
        store = InMemoryCoreStateStore()
        ref = _ref()
        await store.create_conversation(ref)
        old = await store.acquire_authority(CoreInstanceId(value="old"))
        snapshot = TurnSnapshot(identity_revision="i", core_config_revision="c", turn_origin=_origin(), conversation_context_revision="ctx", principal_binding_reference="p", persona_snapshot=_persona())
        command = _command()
        admitted = await store.try_admit_turn(old, ref, TurnId(value="t1"), command.fingerprint, snapshot, command.input)
        assert admitted.status == TurnAdmissionStatus.NEWLY_ADMITTED
        response_id = ResponseId(value="r1")
        response = await store.commit_response_started(old, ref, TurnId(value="t1"), response_id)
        new = await store.acquire_authority(CoreInstanceId(value="new"))
        with pytest.raises(CoreStateError, match="STALE_CORE_AUTHORITY"):
            await store.commit_segment(old, response_id, response.revision, AgentOutputSegmentCommitted(response_id=response_id, segment_id=SegmentId(value="s1"), sequence=1, text="late", language="en"))
        aborted = await store.abort_response(new, response_id, response.revision, "CANCELLED")
        with pytest.raises(CoreStateError, match="RESPONSE_NOT_OPEN"):
            await store.commit_segment(new, response_id, aborted.revision, AgentOutputSegmentCommitted(response_id=response_id, segment_id=SegmentId(value="s1"), sequence=1, text="late", language="en"))
        with pytest.raises(TypeError):
            ResponseEventStream.validate((object(),))
    asyncio.run(scenario())


def test_same_segment_is_idempotent_but_changed_segment_and_late_terminal_delta_are_rejected():
    async def scenario():
        store = InMemoryCoreStateStore()
        ref = _ref()
        await store.create_conversation(ref)
        token = await store.acquire_authority(CoreInstanceId(value="core"))
        command = _command()
        snapshot = TurnSnapshot(identity_revision="i", core_config_revision="c", turn_origin=_origin(), conversation_context_revision="ctx", principal_binding_reference="p", persona_snapshot=_persona())
        await store.try_admit_turn(token, ref, TurnId(value="t1"), command.fingerprint, snapshot, command.input)
        response_id = ResponseId(value="r1")
        started = await store.commit_response_started(token, ref, TurnId(value="t1"), response_id)
        segment = AgentOutputSegmentCommitted(response_id=response_id, segment_id=SegmentId(value="s1"), sequence=1, text="one", language="en")
        committed = await store.commit_segment(token, response_id, started.revision, segment)
        assert (await store.commit_segment(token, response_id, started.revision, segment)).segments == (segment,)
        with pytest.raises(CoreStateError, match="SEGMENT_ID_CONFLICT"):
            await store.commit_segment(token, response_id, started.revision, segment.model_copy(update={"text": "changed"}))
        closed = await store.finalize_response(token, response_id, committed.revision)
        with pytest.raises(CoreStateError, match="RESPONSE_NOT_OPEN"):
            await store.commit_segment(token, response_id, closed.revision, segment)
    asyncio.run(scenario())


def test_provider_fallback_stays_within_response_before_prefix_only_and_releases_claim_atomically():
    async def scenario():
        fallback = FakeModelAdapter(((AttemptTerminal(kind=AttemptTerminalKind.FAILED, reason="first"),), (VisibleTextDelta(text="recovered"), AttemptTerminal(kind=AttemptTerminalKind.SUCCEEDED))))
        core, store, _ = await _core(fallback)
        await core.submit_turn(_command())
        await core.drain()
        response_id = (await core.submit_turn(_command())).response_id
        assert response_id is not None
        recovered = await core.get_response(GetResponseCommand(caller_context=CallerContext(logical_user_id=UserId(value="u1")), response_id=response_id))
        assert len(recovered.attempts) == 2 and recovered.state.value == "finalized"
        assert await store.active_claim(_ref()) is None

        prefix_failure = FakeModelAdapter(((VisibleTextDelta(text="prefix"), AttemptTerminal(kind=AttemptTerminalKind.FAILED, reason="broken")), (VisibleTextDelta(text="must not run"),)))
        core2, _, _ = await _core(prefix_failure)
        await core2.submit_turn(_command(conversation="c1"))
        await core2.drain()
        response_id2 = (await core2.submit_turn(_command(conversation="c1"))).response_id
        aborted = await core2.get_response(GetResponseCommand(caller_context=CallerContext(logical_user_id=UserId(value="u1")), response_id=response_id2))
        assert aborted.state.value == "aborted" and len(aborted.attempts) == 1 and len(prefix_failure.contexts) == 1
    asyncio.run(scenario())


def test_replay_after_cursor_has_no_subscribe_gap_and_cross_user_read_is_denied():
    async def scenario():
        model = FakeModelAdapter(((VisibleTextDelta(text="hi", language="en"), AttemptTerminal(kind=AttemptTerminalKind.SUCCEEDED)),))
        core, _, _ = await _core(model)
        await core.submit_turn(_command())
        await core.drain()
        response_id = (await core.submit_turn(_command())).response_id
        assert response_id is not None
        with pytest.raises(CoreStateError, match="READ_NOT_AUTHORIZED"):
            await core.get_response(GetResponseCommand(caller_context=CallerContext(logical_user_id=UserId(value="u2")), response_id=response_id))
        with pytest.raises(CoreStateError, match="READ_NOT_AUTHORIZED"):
            await core.get_turn(GetTurnCommand(caller_context=CallerContext(logical_user_id=UserId(value="u2")), conversation_ref=_ref(), turn_id=TurnId(value="t1")))
        events = []
        async for event in core.watch_response(WatchResponseCommand(caller_context=CallerContext(logical_user_id=UserId(value="u1")), response_id=response_id, after_event_sequence=1)):
            events.append(event)
        assert [event.event_sequence for event in events] == [2, 3]
    asyncio.run(scenario())


def test_memory_store_explicitly_declares_ephemeral_without_restart_durability():
    assert InMemoryCoreStateStore.persistence_mode == "EPHEMERAL"
