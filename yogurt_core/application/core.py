"""Application facade for the R2-A process-lifetime reference Core."""

import uuid
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Optional, Tuple, TypeVar

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import CoreInstanceId, ExecutionAttemptId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.foundation.facts import CommitCertainty
from yogurt_contracts.response.events import ResponseEvent
from yogurt_contracts.response.lifecycle import ResponseAbortReason, ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken
from yogurt_contracts.foundation.durability import CorePersistenceMode

from yogurt_core.application.commands import CallerContext, CancelTurnCommand, GetResponseCommand, GetTurnCommand, SubmitTurnCommand, WatchResponseCommand
from yogurt_core.application.results import CancelTurnResult, CancelTurnStatus, TurnAdmissionResult, TurnAdmissionStatus
from yogurt_core.authority.conversation_authority import ConversationAuthority
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.context.composition import ContextComposer
from yogurt_core.model.events import AttemptTerminal, AttemptTerminalKind, VisibleTextDelta
from yogurt_core.model.execution import ModelContext, StoredResponse, StoredTurn
from yogurt_core.lifecycle import CoreLifecycleState
from yogurt_core.ports.identity import IdentityResolver
from yogurt_core.ports.model import ModelPort
from yogurt_core.ports.telemetry import TurnObserver
from yogurt_core.model.events import ModelDraftEvent
from yogurt_core.ports.persona import PersonaSnapshotPort
from yogurt_core.ports.state_store import CoreStateError, CoreStateStore, StateCommit
from yogurt_core.response.terminal import consequence_from_response_terminal
from yogurt_core.scheduling.conversation_scheduler import ConversationScheduler


class YogurtCore:
    """Thin orchestration facade. State authority remains exclusively in its store."""

    def __init__(
        self,
        store: CoreStateStore,
        identity_resolver: IdentityResolver,
        persona_snapshots: PersonaSnapshotPort,
        model: ModelPort,
        core_instance_id: CoreInstanceId,
        context_policy: Optional[ContextBudgetPolicy] = None,
        scheduler: Optional[ConversationScheduler] = None,
        observer: Optional[TurnObserver] = None,
    ) -> None:
        self._observer = observer
        self._store = store
        self._identity_resolver = identity_resolver
        self._persona_snapshots = persona_snapshots
        self._model = model
        self._core_instance_id = core_instance_id
        self._context_policy = context_policy or ContextBudgetPolicy()
        self._scheduler = scheduler or ConversationScheduler()
        self._composer = ContextComposer(store, self._context_policy)
        self._token: Optional[CoreAuthorityToken] = None
        self._lifecycle = CoreLifecycleState.STOPPED

    async def start(self) -> CoreAuthorityToken:
        self._lifecycle = CoreLifecycleState.STARTING
        try:
            self._token = await self._store.acquire_authority(self._core_instance_id)
            if getattr(self._store, "persistence_mode", CorePersistenceMode.EPHEMERAL) == CorePersistenceMode.DURABLE:
                self._lifecycle = CoreLifecycleState.RECOVERING
                await self._store.recover(self._token)
            self._lifecycle = CoreLifecycleState.SERVING
            return self._token
        except Exception:
            self._lifecycle = CoreLifecycleState.NOT_SERVING
            raise

    async def stop(self, drain_timeout_seconds: float = 5.0) -> None:
        self._lifecycle = CoreLifecycleState.DRAINING
        try:
            await asyncio.wait_for(self._scheduler.drain(), timeout=drain_timeout_seconds)
        except (asyncio.TimeoutError, Exception):
            await self._scheduler.cancel_remaining()
        finally:
            if self._token is not None:
                try:
                    await self._store.release_authority(self._token)
                finally:
                    self._token = None
            self._lifecycle = CoreLifecycleState.STOPPED

    async def create_conversation(self, conversation_ref: ConversationRef) -> None:
        self._require_serving()
        await ConversationAuthority(self._store).create(self._require_token(), conversation_ref)

    async def submit_turn(self, command: SubmitTurnCommand) -> TurnAdmissionResult:
        self._require_serving()
        token = self._require_token()
        expected_fingerprint = turn_fingerprint(command.input, command.principal_binding_reference)
        if command.fingerprint != expected_fingerprint:
            return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="INVALID_TURN_FINGERPRINT")
        resolution = await self._identity_resolver.resolve(command.external_principal)
        origin = command.input.origin
        if (
            origin.external_principal_ref != command.external_principal.reference
            or origin.identity_resolution_ref != resolution.reference
            or origin.origin_surface != command.external_principal.authenticated_surface
        ):
            return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="TURN_ORIGIN_MISMATCH")
        if resolution.logical_user != command.caller_context.logical_user_id:
            return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="CALLER_IDENTITY_MISMATCH")
        authority = ConversationAuthority(self._store)
        if not await authority.validate_owner(command.conversation_ref, resolution.logical_user):
            return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="CONVERSATION_NOT_OWNED")
        persona = await self._persona_snapshots.get_snapshot(command.conversation_ref.persona_id)
        snapshot = TurnSnapshot(
            identity_revision=resolution.identity_revision,
            core_config_revision=command.core_config_revision,
            turn_origin=origin,
            conversation_context_revision=command.conversation_context_revision,
            principal_binding_reference=command.principal_binding_reference,
            persona_snapshot=persona,
        )
        self._checkpoint(command.conversation_ref, command.turn_id, "core_admission_started")
        commit = await self._store.try_admit_turn_commit(
            token, command.conversation_ref, command.turn_id, expected_fingerprint, snapshot, command.input
        )
        result = self._commit_value(commit)
        if result.status == TurnAdmissionStatus.NEWLY_ADMITTED:
            self._checkpoint(command.conversation_ref, command.turn_id, "core_admitted")
            self._scheduler.schedule(self._scheduler_key(command.conversation_ref, command.turn_id), self._run_turn(command.conversation_ref, command.turn_id))
        return result

    async def cancel_turn(self, command: CancelTurnCommand) -> CancelTurnResult:
        self._require_serving()
        token = self._require_token()
        turn = await self._store.get_authorized_turn(
            command.conversation_ref, command.turn_id, command.caller_context.logical_user_id
        )
        if turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}:
            return CancelTurnResult(status=CancelTurnStatus.ALREADY_TERMINAL, turn_id=turn.turn_ref.turn_id, response_id=turn.response_id)
        if turn.response_id is not None:
            response = await self._store.get_authorized_response(turn.response_id, command.caller_context.logical_user_id)
            if response.state == ResponseState.STARTED:
                closed = await self._abort_with_retry(
                    token, response, ResponseAbortReason.CANCELLED, command.caller_context.logical_user_id
                )
                if closed.state == ResponseState.ABORTED and closed.abort_reason == ResponseAbortReason.CANCELLED:
                    if closed.attempts:
                        await self._model.cancel(closed.attempts[-1])
                    await self._converge_turn_from_response_terminal(command.conversation_ref, command.turn_id, closed)
                    return CancelTurnResult(status=CancelTurnStatus.CANCELLED, turn_id=turn.turn_ref.turn_id, response_id=turn.response_id)
                await self._converge_turn_from_response_terminal(command.conversation_ref, command.turn_id, closed)
                return CancelTurnResult(status=CancelTurnStatus.ALREADY_TERMINAL, turn_id=turn.turn_ref.turn_id, response_id=turn.response_id)
            await self._converge_turn_from_response_terminal(command.conversation_ref, command.turn_id, response)
            return CancelTurnResult(status=CancelTurnStatus.ALREADY_TERMINAL, turn_id=turn.turn_ref.turn_id, response_id=turn.response_id)
        await self._terminalize(command.conversation_ref, command.turn_id, TurnState.CANCELLED)
        return CancelTurnResult(status=CancelTurnStatus.CANCELLED, turn_id=turn.turn_ref.turn_id)

    async def get_turn(self, command: GetTurnCommand) -> StoredTurn:
        return await self._store.get_authorized_turn(command.conversation_ref, command.turn_id, command.caller_context.logical_user_id)

    async def get_response(self, command: GetResponseCommand) -> StoredResponse:
        return await self._store.get_authorized_response(command.response_id, command.caller_context.logical_user_id)

    async def response_conversation(self, command: GetResponseCommand) -> ConversationRef:
        await self.get_response(command)
        return (await self._store.get_turn_by_response(command.response_id)).conversation_ref

    async def watch_response(self, command: WatchResponseCommand) -> AsyncIterator[ResponseEvent]:
        await self._store.get_authorized_response(command.response_id, command.caller_context.logical_user_id)
        async for event in self._store.watch_response_events(command.response_id, command.after_event_sequence):
            yield event

    async def drain(self) -> None:
        await self._scheduler.drain()

    async def _run_turn(self, conversation_ref: ConversationRef, turn_id: TurnId) -> None:
        response: Optional[StoredResponse] = None
        try:
            token = self._require_token()
            turn = await self._store.get_turn(conversation_ref, turn_id)
            response_id = ResponseId(value="response-" + uuid.uuid4().hex)
            response = await self._store.commit_response_started(token, conversation_ref, turn.turn_ref.turn_id, response_id)
            # Yield after durable response creation so ingress can return acceptance.
            await asyncio.sleep(0)
            context = await self._composer.compose(turn)
            saw_committed_prefix = False
            for _ in (1, 2):
                attempt = ExecutionAttemptSnapshot(
                    execution_attempt_id=ExecutionAttemptId(value="attempt-" + uuid.uuid4().hex),
                    response_id=response_id,
                    binding_snapshots=(),
                    started_at=UtcInstant(value=datetime.now(timezone.utc)),
                    provider_applied_config_revision="r2a",
                )
                response = await self._store.add_execution_attempt(token, response_id, attempt)
                terminal = AttemptTerminal(kind=AttemptTerminalKind.SUCCEEDED)
                async for draft in self._observed_model(conversation_ref, turn_id, context, attempt):
                    if isinstance(draft, VisibleTextDelta):
                        text = draft.text.strip()
                        if not text:
                            continue
                        segment = AgentOutputSegmentCommitted(
                            response_id=response_id,
                            segment_id=SegmentId(value="segment-" + uuid.uuid4().hex),
                            sequence=len(response.segments) + 1,
                            text=text,
                            language=draft.language,
                        )
                        response = self._commit_value(await self._store.commit_segment_commit(token, response_id, response.revision, segment))
                        saw_committed_prefix = True
                    elif isinstance(draft, AttemptTerminal):
                        terminal = draft
                if terminal.kind == AttemptTerminalKind.SUCCEEDED:
                    response = await self._store.finalize_response(token, response_id, response.revision)
                    await self._converge_turn_from_response_terminal(conversation_ref, turn.turn_ref.turn_id, response)
                    return
                if saw_committed_prefix or terminal.kind == AttemptTerminalKind.CANCELLED or _ == 2:
                    abort_reason = ResponseAbortReason.CANCELLED if terminal.kind == AttemptTerminalKind.CANCELLED else ResponseAbortReason.PROVIDER_FAILURE
                    response = await self._store.abort_response(token, response_id, response.revision, abort_reason)
                    await self._converge_turn_from_response_terminal(conversation_ref, turn.turn_ref.turn_id, response)
                    return
        except CoreStateError as error:
            if error.code in {"STALE_CORE_AUTHORITY", "RESPONSE_NOT_OPEN", "TURN_NOT_OPEN"}:
                return
            if error.code == "TURN_REVISION_CONFLICT" and await self._turn_is_terminal(conversation_ref, turn_id):
                return
            raise
        except Exception as original_error:
            try:
                await self._converge_execution_failure(conversation_ref, turn_id, response)
            except Exception as cleanup_error:
                # Preserve cleanup diagnostics without replacing the execution
                # failure surfaced by the scheduler on every supported Python.
                raise original_error from cleanup_error
            raise

        finally:
            # Read the authoritative terminal fact, never infer terminal from transport.
            if response is not None and self._observer is not None:
                try:
                    latest = await self._store.get_response(response.response_id)
                    if latest.state in {ResponseState.FINALIZED, ResponseState.ABORTED}:
                        self._checkpoint(conversation_ref, turn_id, "response_terminal")
                except Exception:
                    pass

    def _checkpoint(self, ref: ConversationRef, turn_id: TurnId, name: str) -> None:
        if self._observer is not None:
            try:
                self._observer.checkpoint(ref, turn_id, name)
            except Exception:
                pass  # Telemetry never changes admission/execution outcomes.

    async def _observed_model(self, ref: ConversationRef, turn_id: TurnId,
                              context: ModelContext, attempt: ExecutionAttemptSnapshot) -> AsyncIterator[ModelDraftEvent]:
        self._checkpoint(ref, turn_id, "model_started")
        try:
            async for event in self._model.run(context, attempt):
                if isinstance(event, VisibleTextDelta):
                    self._checkpoint(ref, turn_id, "first_model_output")
                yield event
        finally:
            self._checkpoint(ref, turn_id, "model_finished")

    async def list_conversations(self, caller: "CallerContext") -> Tuple[ConversationRef, ...]:
        self._require_serving()
        return await self._store.list_conversations(caller.logical_user_id)

    async def conversation_turns(self, ref: ConversationRef, caller: "CallerContext") -> Tuple[StoredTurn, ...]:
        self._require_serving()
        return await self._store.conversation_turns(ref, caller.logical_user_id)

    async def _converge_execution_failure(
        self, conversation_ref: ConversationRef, turn_id: TurnId, response: Optional[StoredResponse]
    ) -> None:
        token = self._require_token()
        if response is None:
            turn = await self._store.get_turn(conversation_ref, turn_id)
            if turn.response_id is not None:
                response = await self._store.get_response(turn.response_id)
        if response is not None and response.state == ResponseState.STARTED:
            response = await self._abort_with_retry(
                token, response, ResponseAbortReason.EXECUTION_FAILURE, None
            )
        if response is not None and response.state in {ResponseState.FINALIZED, ResponseState.ABORTED}:
            await self._converge_turn_from_response_terminal(conversation_ref, turn_id, response)
            return
        if not await self._turn_is_terminal(conversation_ref, turn_id):
            await self._terminalize(conversation_ref, turn_id, TurnState.ABORTED, AbortReason.EXECUTION_FAILURE)

    async def _converge_turn_from_response_terminal(
        self, conversation_ref: ConversationRef, turn_id: TurnId, response: StoredResponse
    ) -> None:
        self._checkpoint(conversation_ref, turn_id, "response_terminal")
        consequence = consequence_from_response_terminal(response.state, response.abort_reason)
        turn = await self._store.get_turn(conversation_ref, turn_id)
        if turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}:
            return
        try:
            self._commit_value(await self._store.terminalize_turn_commit(
                self._require_token(), conversation_ref, turn_id, turn.revision, consequence.state, consequence.abort_reason,
            ))
        except CoreStateError as error:
            if error.code == "TURN_REVISION_CONFLICT" and await self._turn_is_terminal(conversation_ref, turn_id):
                return
            raise

    async def _turn_is_terminal(self, conversation_ref: ConversationRef, turn_id: TurnId) -> bool:
        turn = await self._store.get_turn(conversation_ref, turn_id)
        return turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}

    async def _abort_with_retry(
        self,
        token: CoreAuthorityToken,
        response: StoredResponse,
        reason: ResponseAbortReason,
        caller_user_id: Optional[UserId],
    ) -> StoredResponse:
        while True:
            try:
                return await self._store.abort_response(token, response.response_id, response.revision, reason)
            except CoreStateError as error:
                if error.code not in {"RESPONSE_REVISION_CONFLICT", "RESPONSE_NOT_OPEN"}:
                    raise
                if caller_user_id is None:
                    response = await self._store.get_response(response.response_id)
                else:
                    response = await self._store.get_authorized_response(response.response_id, caller_user_id)
                if response.state in {ResponseState.FINALIZED, ResponseState.ABORTED}:
                    return response
                if error.code == "RESPONSE_NOT_OPEN":
                    raise

    async def _terminalize(
        self, conversation_ref: ConversationRef, turn_id: TurnId, state: TurnState, abort_reason: Optional[AbortReason] = None
    ) -> None:
        turn = await self._store.get_turn(conversation_ref, turn_id)
        try:
            self._commit_value(await self._store.terminalize_turn_commit(
                self._require_token(), conversation_ref, turn.turn_ref.turn_id, turn.revision, state, abort_reason
            ))
        except CoreStateError as error:
            if error.code == "TURN_REVISION_CONFLICT" and await self._turn_is_terminal(conversation_ref, turn_id):
                return
            raise

    def _require_token(self) -> CoreAuthorityToken:
        if self._token is None:
            raise RuntimeError("Core has not acquired authority")
        return self._token

    def _require_serving(self) -> None:
        if self._lifecycle != CoreLifecycleState.SERVING:
            raise CoreStateError("CORE_NOT_SERVING")

    _CommittedValue = TypeVar("_CommittedValue")

    @staticmethod
    def _commit_value(commit: StateCommit[_CommittedValue]) -> _CommittedValue:
        if commit.certainty == CommitCertainty.NOT_COMMITTED or commit.value is None:
            raise CoreStateError("STATE_NOT_COMMITTED")
        # OUTCOME_UNKNOWN is reconciled by the adapter using the identical
        # stable identity before returning its durable projection value.
        return commit.value

    @staticmethod
    def _scheduler_key(conversation_ref: ConversationRef, turn_id: TurnId) -> str:
        return conversation_ref.conversation_id.value + ":" + turn_id.value
