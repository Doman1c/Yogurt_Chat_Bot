"""Behavioral evidence for the R2-A ephemeral Core hardening gate."""

import asyncio

import pytest

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.input import TextInputPart, TurnInput
from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import ConversationRef, TurnOrigin
from yogurt_contracts.execution.persona import PersonaSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.durability import CorePersistenceMode
from yogurt_contracts.foundation.identifiers import CoreInstanceId, ConversationId, ExternalPrincipalRef, IdentityResolutionRef, InputId, PersonaId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.identity.principal import ExternalPrincipal
from yogurt_contracts.identity.resolution import IdentityResolution
from yogurt_contracts.response.lifecycle import ResponseAbortReason, ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_core.application.commands import CallerContext, CancelTurnCommand, GetResponseCommand, GetTurnCommand, SubmitTurnCommand, WatchResponseCommand
from yogurt_core.application.core import YogurtCore
from yogurt_core.application.results import CancelTurnStatus, TurnAdmissionStatus
from yogurt_core.context.models import ContextEntry, ContextProjection, ContextRole
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.model.events import AttemptTerminal, AttemptTerminalKind, VisibleTextDelta
from yogurt_core.response.stream import ResponseEventStream
from yogurt_core.scheduling.conversation_scheduler import ConversationScheduler
from yogurt_core_adapters.identity_fake import FakeIdentityResolver
from yogurt_core_adapters.model_fake import FakeModelAdapter
from yogurt_core_adapters.persona_fake import FakePersonaSnapshotPort
from yogurt_core_adapters.state_store_memory import CoreStateError, InMemoryCoreStateStore


def _ref(user: str = "u1", conversation: str = "c1") -> ConversationRef:
    return ConversationRef(persona_id=PersonaId(value="yogurt"), user_id=UserId(value=user), conversation_id=ConversationId(value=conversation))


def _origin(principal: str = "external-u1", resolution: str = "res-u1", surface: str = "test") -> TurnOrigin:
    return TurnOrigin(origin_surface=surface, external_principal_ref=ExternalPrincipalRef(value=principal), identity_resolution_ref=IdentityResolutionRef(value=resolution))


def _persona() -> PersonaSnapshot:
    return PersonaSnapshot(persona_id=PersonaId(value="yogurt"), persona_revision="p1", soul_revision="s1", identity_revision="i1", relationship_context_revision="r1", model_context_material="persona")


def _command(turn: str = "t1", text: str = "hello", origin: TurnOrigin = None) -> SubmitTurnCommand:
    actual_origin = origin or _origin()
    turn_input = TurnInput(input_id=InputId(value="input-" + turn), parts=(TextInputPart(text=text),), origin=actual_origin)
    return SubmitTurnCommand(
        caller_context=CallerContext(logical_user_id=UserId(value="u1")),
        external_principal=ExternalPrincipal(reference=ExternalPrincipalRef(value="external-u1"), authenticated_surface="test"),
        conversation_ref=_ref(), turn_id=TurnId(value=turn), fingerprint=turn_fingerprint(turn_input, "binding"), input=turn_input,
        core_config_revision="config", conversation_context_revision="context", principal_binding_reference="binding",
    )


async def _core(model=None, scheduler=None, context_policy=None, store=None):
    store = store or InMemoryCoreStateStore()
    identity = FakeIdentityResolver({"external-u1": IdentityResolution(reference=IdentityResolutionRef(value="res-u1"), logical_user=UserId(value="u1"), identity_revision="identity", source_hash="source")})
    core = YogurtCore(store, identity, FakePersonaSnapshotPort({"yogurt": _persona()}), model or FakeModelAdapter(((),)), CoreInstanceId(value="core"), context_policy=context_policy, scheduler=scheduler)
    await core.start()
    await core.create_conversation(_ref())
    return core, store


def test_authority_is_fail_closed_explicit_handoff_and_fences_lifecycle_writes():
    async def scenario():
        store = InMemoryCoreStateStore()
        old = await store.acquire_authority(CoreInstanceId(value="old"))
        with pytest.raises(CoreStateError, match="CORE_AUTHORITY_ALREADY_OWNED"):
            await store.acquire_authority(CoreInstanceId(value="new"))
        await store.create_conversation(old, _ref())
        await store.release_authority(old)
        new = await store.acquire_authority(CoreInstanceId(value="new"))
        assert new.authority_epoch > old.authority_epoch
        with pytest.raises(CoreStateError, match="STALE_CORE_AUTHORITY"):
            await store.create_conversation(old, _ref(conversation="stale"))
        with pytest.raises(CoreStateError, match="STALE_CORE_AUTHORITY"):
            await store.try_admit_turn(old, _ref(), TurnId(value="t"), _command().fingerprint, _snapshot(), _command().input)
        await store.create_conversation(new, _ref(conversation="new"))
    asyncio.run(scenario())


def test_create_conversation_requires_current_authority_token():
    async def scenario():
        store = InMemoryCoreStateStore()
        with pytest.raises(CoreStateError, match="STALE_CORE_AUTHORITY"):
            await store.create_conversation(None, _ref())  # type: ignore[arg-type]
    asyncio.run(scenario())


def test_terminalization_validates_claim_before_writing_and_normal_release_is_atomic():
    async def scenario():
        store, token = await _admitted_store()
        ref = _ref()
        key = "yogurt|u1|c1|t1"
        store._claims["yogurt|u1|c1"] = "wrong"  # intentional fault injection at transaction boundary
        before = await store.get_turn(ref, TurnId(value="t1"))
        with pytest.raises(CoreStateError, match="CONVERSATION_CLAIM_MISMATCH"):
            await store.terminalize_turn(token, ref, TurnId(value="t1"), before.revision, TurnState.CANCELLED)
        unchanged = await store.get_turn(ref, TurnId(value="t1"))
        assert unchanged == before and store._claims["yogurt|u1|c1"] == "wrong"
        store._claims["yogurt|u1|c1"] = key
        terminal = await store.terminalize_turn(token, ref, TurnId(value="t1"), unchanged.revision, TurnState.CANCELLED)
        assert terminal.state == TurnState.CANCELLED and await store.active_claim(ref) is None
    asyncio.run(scenario())


def test_turn_lifecycle_transitions_are_authoritative_and_validate_r1_graph():
    async def scenario():
        store, token = await _admitted_store()
        admitted = await store.get_turn(_ref(), TurnId(value="t1"))
        assert admitted.state == TurnState.INPUT_COMMITTED
        with pytest.raises(CoreStateError, match="INVALID_TURN_TRANSITION"):
            await store.terminalize_turn(token, _ref(), TurnId(value="t1"), admitted.revision, TurnState.COMPLETED)
        assert (await store.get_turn(_ref(), TurnId(value="t1"))).state == TurnState.INPUT_COMMITTED
        assert await store.active_claim(_ref()) == TurnId(value="t1")
        await store.commit_response_started(token, _ref(), TurnId(value="t1"), ResponseId(value="r1"))
        assert (await store.get_turn(_ref(), TurnId(value="t1"))).state == TurnState.PROCESSING

        core, core_store = await _core(FakeModelAdapter(((VisibleTextDelta(text="ok"),),)))
        command = _command()
        await core.submit_turn(command)
        await core.drain()
        assert (await core_store.get_turn(_ref(), command.turn_id)).state == TurnState.COMPLETED
    asyncio.run(scenario())


def test_cancel_turn_before_and_after_response_started_and_after_terminal():
    async def scenario():
        core, store = await _core()
        command = _command()
        await core.submit_turn(command)
        cancelled = await core.cancel_turn(CancelTurnCommand(caller_context=command.caller_context, conversation_ref=_ref(), turn_id=command.turn_id))
        assert cancelled.status == CancelTurnStatus.CANCELLED and cancelled.response_id is None
        assert (await store.get_turn(_ref(), command.turn_id)).state == TurnState.CANCELLED
        await core.drain()

        entered, release = asyncio.Event(), asyncio.Event()
        model = _BlockingModel(entered, release)
        core2, store2 = await _core(model)
        command2 = _command()
        await core2.submit_turn(command2)
        await entered.wait()
        response_id = (await core2.submit_turn(command2)).response_id
        result = await core2.cancel_turn(CancelTurnCommand(caller_context=command2.caller_context, conversation_ref=_ref(), turn_id=command2.turn_id))
        assert result.status == CancelTurnStatus.CANCELLED
        assert (await core2.get_response(GetResponseCommand(caller_context=command2.caller_context, response_id=response_id))).state == ResponseState.ABORTED
        assert (await store2.get_turn(_ref(), command2.turn_id)).state == TurnState.CANCELLED
        release.set()
        await core2.drain()
        assert (await core2.cancel_turn(CancelTurnCommand(caller_context=command2.caller_context, conversation_ref=_ref(), turn_id=command2.turn_id))).status == CancelTurnStatus.ALREADY_TERMINAL
    asyncio.run(scenario())


def test_cancel_converges_from_finalized_and_typed_aborted_response_terminal_facts():
    async def scenario():
        async def prepare_terminal(response_reason=None):
            core, store = await _core()
            token = core._token
            command = _command()
            await store.try_admit_turn(token, _ref(), command.turn_id, command.fingerprint, _snapshot(), command.input)
            started = await store.commit_response_started(token, _ref(), command.turn_id, ResponseId(value="r1"))
            if response_reason is None:
                await store.finalize_response(token, started.response_id, started.revision)
            else:
                await store.abort_response(token, started.response_id, started.revision, response_reason)
            assert (await store.get_turn(_ref(), command.turn_id)).state == TurnState.PROCESSING
            return core, store, command

        core, store, command = await prepare_terminal()
        result = await core.cancel_turn(CancelTurnCommand(caller_context=command.caller_context, conversation_ref=_ref(), turn_id=command.turn_id))
        assert result.status == CancelTurnStatus.ALREADY_TERMINAL
        assert (await store.get_turn(_ref(), command.turn_id)).state == TurnState.COMPLETED

        for response_reason, expected_reason in ((ResponseAbortReason.PROVIDER_FAILURE, AbortReason.PROVIDER_FAILURE), (ResponseAbortReason.EXECUTION_FAILURE, AbortReason.EXECUTION_FAILURE)):
            core, store, command = await prepare_terminal(response_reason)
            result = await core.cancel_turn(CancelTurnCommand(caller_context=command.caller_context, conversation_ref=_ref(), turn_id=command.turn_id))
            turn = await store.get_turn(_ref(), command.turn_id)
            assert result.status == CancelTurnStatus.ALREADY_TERMINAL
            assert turn.state == TurnState.ABORTED and turn.abort_reason == expected_reason
    asyncio.run(scenario())


def test_fingerprint_and_origin_are_verified_before_admission():
    async def scenario():
        core, store = await _core()
        command = _command()
        changed_input = command.input.model_copy(update={"parts": (TextInputPart(text="changed"),)})
        assert (await core.submit_turn(command.model_copy(update={"input": changed_input}))).reason == "INVALID_TURN_FINGERPRINT"
        for origin in (_origin(principal="forged"), _origin(resolution="forged"), _origin(surface="forged")):
            forged = _command(origin=origin)
            assert (await core.submit_turn(forged)).reason == "TURN_ORIGIN_MISMATCH"
        assert await store.active_claim(_ref()) is None
    asyncio.run(scenario())


def test_authenticated_principal_and_response_absence_are_fail_closed():
    async def scenario():
        core, store = await _core()
        command = _command()
        forged_caller = command.model_copy(update={"caller_context": CallerContext(logical_user_id=UserId(value="other-user"))})
        rejected = await core.submit_turn(forged_caller)
        assert rejected.status == TurnAdmissionStatus.REJECTED and rejected.reason == "CALLER_IDENTITY_MISMATCH"
        assert await store.active_claim(_ref()) is None

        admitted = await core.submit_turn(command)
        assert admitted.response_id is None
        watcher = core.watch_response(WatchResponseCommand(caller_context=command.caller_context, response_id=ResponseId(value="not-started")))
        with pytest.raises(CoreStateError, match="RESPONSE_NOT_FOUND"):
            await watcher.__anext__()
        assert (await store.get_turn(_ref(), command.turn_id)).response_id is None
    asyncio.run(scenario())


def test_stop_is_bounded_and_releases_authority_after_timeout_or_drain_failure():
    class HangingScheduler:
        def __init__(self) -> None:
            self.cancelled = False

        async def drain(self) -> None:
            await asyncio.Event().wait()

        async def cancel_remaining(self) -> None:
            self.cancelled = True

    class FailingScheduler(HangingScheduler):
        async def drain(self) -> None:
            raise RuntimeError("drain failed")

    async def scenario():
        for scheduler in (HangingScheduler(), FailingScheduler()):
            core, store = await _core(scheduler=scheduler)
            await core.stop(drain_timeout_seconds=0.01)
            assert scheduler.cancelled
            replacement = await store.acquire_authority(CoreInstanceId(value="replacement"))
            await store.release_authority(replacement)
    asyncio.run(scenario())


def test_context_is_ordered_and_current_input_is_not_duplicated():
    async def scenario():
        model = FakeModelAdapter(((VisibleTextDelta(text="A1"),), (VisibleTextDelta(text="A2"),), (VisibleTextDelta(text="A3"),)))
        core, _ = await _core(model)
        for turn, text in (("t1", "U1"), ("t2", "U2"), ("t3", "U3")):
            await core.submit_turn(_command(turn, text))
            await core.drain()
        context = model.contexts[2]
        assert [(entry.role, entry.text) for entry in context.history] == [
            (ContextRole.USER, "U1"), (ContextRole.ASSISTANT, "A1"),
            (ContextRole.USER, "U2"), (ContextRole.ASSISTANT, "A2"),
        ]
        assert context.user_input == "U3" and all(entry.text != "U3" for entry in context.history)
        policy = ContextBudgetPolicy(budget=2)
        projected = ContextProjection(entries=tuple(context.history), conversation_metadata="x")
        turn = await core.get_turn(GetTurnCommand(caller_context=CallerContext(logical_user_id=UserId(value="u1")), conversation_ref=_ref(), turn_id=TurnId(value="t3")))
        assert [entry.text for entry in policy.compose(projected, turn).history] == ["U2", "A2"]
    asyncio.run(scenario())


def test_provider_exceptions_abort_deterministically_and_scheduler_surfaces_unexpected_errors():
    async def scenario():
        for events in ((RuntimeError("before"),), (VisibleTextDelta(text="prefix"), RuntimeError("after"))):
            core, store = await _core(_RaisingModel(events))
            command = _command()
            await core.submit_turn(command)
            with pytest.raises(RuntimeError, match="before|after"):
                await core.drain()
            response_id = (await core.submit_turn(command)).response_id
            response = await core.get_response(GetResponseCommand(caller_context=command.caller_context, response_id=response_id))
            assert response.state == ResponseState.ABORTED and await store.active_claim(_ref()) is None
            assert response.abort_reason == ResponseAbortReason.EXECUTION_FAILURE
            turn = await store.get_turn(_ref(), command.turn_id)
            assert turn.state == TurnState.ABORTED and turn.abort_reason == AbortReason.EXECUTION_FAILURE
            assert len(response.segments) == (0 if isinstance(events[0], RuntimeError) else 1)

        scheduler = ConversationScheduler()
        async def broken():
            raise ValueError("unexpected")
        scheduler.schedule("broken", broken())
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match="unexpected"):
            await scheduler.drain()
    asyncio.run(scenario())


def test_context_policy_failure_after_response_started_converges_then_reraises_original_error():
    async def scenario():
        core, store = await _core(context_policy=_FailingContextPolicy())
        command = _command()
        await core.submit_turn(command)
        with pytest.raises(ValueError, match="context failed"):
            await core.drain()
        response_id = (await core.submit_turn(command)).response_id
        response = await core.get_response(GetResponseCommand(caller_context=command.caller_context, response_id=response_id))
        turn = await store.get_turn(_ref(), command.turn_id)
        assert response.state == ResponseState.ABORTED and response.abort_reason == ResponseAbortReason.EXECUTION_FAILURE
        assert turn.state == TurnState.ABORTED and turn.abort_reason == AbortReason.EXECUTION_FAILURE
        assert await store.active_claim(_ref()) is None
    asyncio.run(scenario())


def test_cancel_race_reloads_finalized_or_provider_aborted_response_before_converging_turn():
    async def scenario():
        for winner, expected_state, expected_reason in (
            ("finalized", TurnState.COMPLETED, None),
            ("provider", TurnState.ABORTED, AbortReason.PROVIDER_FAILURE),
        ):
            store = _TerminalWinningStore(winner)
            core, _ = await _core(store=store)
            command = _command()
            token = core._token
            await store.try_admit_turn(token, _ref(), command.turn_id, command.fingerprint, _snapshot(), command.input)
            await store.commit_response_started(token, _ref(), command.turn_id, ResponseId(value="r1"))
            result = await core.cancel_turn(CancelTurnCommand(caller_context=command.caller_context, conversation_ref=_ref(), turn_id=command.turn_id))
            turn = await store.get_turn(_ref(), command.turn_id)
            assert result.status == CancelTurnStatus.ALREADY_TERMINAL
            assert turn.state == expected_state and turn.abort_reason == expected_reason
            assert await store.active_claim(_ref()) is None
    asyncio.run(scenario())


def test_execution_cleanup_handles_terminal_race_and_reraises_original_failure():
    async def scenario():
        store = _TerminalWinningStore("provider")
        core, _ = await _core(_RaisingModel((RuntimeError("original execution failure"),)), store=store)
        command = _command()
        await core.submit_turn(command)
        with pytest.raises(RuntimeError, match="original execution failure"):
            await core.drain()
        response_id = (await core.submit_turn(command)).response_id
        response = await core.get_response(GetResponseCommand(caller_context=command.caller_context, response_id=response_id))
        turn = await store.get_turn(_ref(), command.turn_id)
        assert response.abort_reason == ResponseAbortReason.PROVIDER_FAILURE
        assert turn.state == TurnState.ABORTED and turn.abort_reason == AbortReason.PROVIDER_FAILURE
        assert await store.active_claim(_ref()) is None
    asyncio.run(scenario())


def test_watcher_terminal_cursor_and_live_handoff_do_not_lose_events():
    async def scenario():
        store, token = await _admitted_store()
        response_id = ResponseId(value="r1")
        started = await store.commit_response_started(token, _ref(), TurnId(value="t1"), response_id)
        watcher = store.watch_response_events(response_id, 0)
        assert (await watcher.__anext__()).event_sequence == 1
        waiting = asyncio.create_task(watcher.__anext__())
        await asyncio.sleep(0)
        segment = AgentOutputSegmentCommitted(response_id=response_id, segment_id=SegmentId(value="s1"), sequence=1, text="N+1", language="en")
        committed = await store.commit_segment(token, response_id, started.revision, segment)
        assert (await waiting).event_sequence == 2
        final_wait = asyncio.create_task(watcher.__anext__())
        await asyncio.sleep(0)
        await store.finalize_response(token, response_id, committed.revision)
        assert (await final_wait).event_sequence == 3
        terminal_cursor = store.watch_response_events(response_id, 3)
        with pytest.raises(StopAsyncIteration):
            await terminal_cursor.__anext__()
        with pytest.raises(TypeError):
            ResponseEventStream.validate((object(),))
    asyncio.run(scenario())


def test_memory_store_is_explicitly_ephemeral():
    assert InMemoryCoreStateStore.persistence_mode == CorePersistenceMode.EPHEMERAL


def _snapshot() -> TurnSnapshot:
    return TurnSnapshot(identity_revision="i", core_config_revision="c", turn_origin=_origin(), conversation_context_revision="ctx", principal_binding_reference="binding", persona_snapshot=_persona())


async def _admitted_store():
    store = InMemoryCoreStateStore()
    token = await store.acquire_authority(CoreInstanceId(value="core"))
    await store.create_conversation(token, _ref())
    command = _command()
    await store.try_admit_turn(token, _ref(), TurnId(value="t1"), command.fingerprint, _snapshot(), command.input)
    return store, token


class _BlockingModel:
    def __init__(self, entered, release):
        self._entered, self._release = entered, release

    async def run(self, context, attempt):
        self._entered.set()
        await self._release.wait()
        yield VisibleTextDelta(text="late")

    async def cancel(self, attempt):
        return None


class _RaisingModel:
    def __init__(self, events):
        self._events = events

    async def run(self, context, attempt):
        for event in self._events:
            if isinstance(event, Exception):
                raise event
            yield event

    async def cancel(self, attempt):
        return None


class _FailingContextPolicy(ContextBudgetPolicy):
    def compose(self, projection, turn):
        raise ValueError("context failed")


class _TerminalWinningStore(InMemoryCoreStateStore):
    """Injects an authoritative terminal winner between cancel/cleanup read and abort CAS."""

    def __init__(self, winner):
        super().__init__()
        self._winner = winner
        self._injected = False

    async def abort_response(self, token, response_id, expected_response_revision, reason):
        if not self._injected:
            self._injected = True
            if self._winner == "finalized":
                await super().finalize_response(token, response_id, expected_response_revision)
            else:
                await super().abort_response(token, response_id, expected_response_revision, ResponseAbortReason.PROVIDER_FAILURE)
        return await super().abort_response(token, response_id, expected_response_revision, reason)
