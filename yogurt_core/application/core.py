"""Application facade for the R2-A process-lifetime reference Core."""

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from yogurt_contracts.conversation.fingerprint import turn_fingerprint
from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import CoreInstanceId, ExecutionAttemptId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.foundation.time import UtcInstant
from yogurt_contracts.response.events import ResponseEvent
from yogurt_contracts.response.lifecycle import ResponseAbortReason, ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken

from yogurt_core.application.commands import CancelTurnCommand, GetResponseCommand, GetTurnCommand, SubmitTurnCommand, WatchResponseCommand
from yogurt_core.application.results import CancelTurnResult, CancelTurnStatus, TurnAdmissionResult, TurnAdmissionStatus
from yogurt_core.authority.conversation_authority import ConversationAuthority
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.context.projection import ContextProjectionService
from yogurt_core.model.events import AttemptTerminal, AttemptTerminalKind, VisibleTextDelta
from yogurt_core.model.execution import StoredResponse, StoredTurn
from yogurt_core.ports.identity import IdentityResolver
from yogurt_core.ports.model import ModelPort
from yogurt_core.ports.persona import PersonaSnapshotPort
from yogurt_core.ports.state_store import CoreStateError, CoreStateStore
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
    ) -> None:
        self._store = store
        self._identity_resolver = identity_resolver
        self._persona_snapshots = persona_snapshots
        self._model = model
        self._core_instance_id = core_instance_id
        self._context_policy = context_policy or ContextBudgetPolicy()
        self._scheduler = scheduler or ConversationScheduler()
        self._projection = ContextProjectionService(store)
        self._token: Optional[CoreAuthorityToken] = None

    async def start(self) -> CoreAuthorityToken:
        self._token = await self._store.acquire_authority(self._core_instance_id)
        return self._token

    async def stop(self) -> None:
        if self._token is not None:
            await self._store.release_authority(self._token)
            self._token = None

    async def create_conversation(self, conversation_ref: ConversationRef) -> None:
        await ConversationAuthority(self._store).create(self._require_token(), conversation_ref)

    async def submit_turn(self, command: SubmitTurnCommand) -> TurnAdmissionResult:
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
        result = await self._store.try_admit_turn(
            token, command.conversation_ref, command.turn_id, expected_fingerprint, snapshot, command.input
        )
        if result.status == TurnAdmissionStatus.NEWLY_ADMITTED:
            self._scheduler.schedule(self._scheduler_key(command.conversation_ref, command.turn_id), self._run_turn(command.conversation_ref, command.turn_id))
        return result

    async def cancel_turn(self, command: CancelTurnCommand) -> CancelTurnResult:
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
                if closed.attempts:
                    await self._model.cancel(closed.attempts[-1])
                await self._converge_turn_from_response_terminal(command.conversation_ref, command.turn_id, closed)
                return CancelTurnResult(status=CancelTurnStatus.CANCELLED, turn_id=turn.turn_ref.turn_id, response_id=turn.response_id)
            await self._converge_turn_from_response_terminal(command.conversation_ref, command.turn_id, response)
            return CancelTurnResult(status=CancelTurnStatus.ALREADY_TERMINAL, turn_id=turn.turn_ref.turn_id, response_id=turn.response_id)
        await self._terminalize(command.conversation_ref, command.turn_id, TurnState.CANCELLED)
        return CancelTurnResult(status=CancelTurnStatus.CANCELLED, turn_id=turn.turn_ref.turn_id)

    async def get_turn(self, command: GetTurnCommand) -> StoredTurn:
        return await self._store.get_authorized_turn(command.conversation_ref, command.turn_id, command.caller_context.logical_user_id)

    async def get_response(self, command: GetResponseCommand) -> StoredResponse:
        return await self._store.get_authorized_response(command.response_id, command.caller_context.logical_user_id)

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
            projection = await self._projection.project(turn)
            context = self._context_policy.compose(projection, turn)
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
                async for draft in self._model.run(context, attempt):
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
                        response = await self._store.commit_segment(token, response_id, response.revision, segment)
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
        except Exception:
            try:
                await self._converge_execution_failure(conversation_ref, turn_id, response)
            except Exception:
                # Cleanup must never mask the original execution failure.
                pass
            raise

    async def _converge_execution_failure(
        self, conversation_ref: ConversationRef, turn_id: TurnId, response: Optional[StoredResponse]
    ) -> None:
        token = self._require_token()
        if response is None:
            turn = await self._store.get_turn(conversation_ref, turn_id)
            if turn.response_id is not None:
                response = await self._store.get_response(turn.response_id)
        if response is not None and response.state == ResponseState.STARTED:
            response = await self._store.abort_response(
                token, response.response_id, response.revision, ResponseAbortReason.EXECUTION_FAILURE
            )
        if response is not None and response.state in {ResponseState.FINALIZED, ResponseState.ABORTED}:
            await self._converge_turn_from_response_terminal(conversation_ref, turn_id, response)
            return
        if not await self._turn_is_terminal(conversation_ref, turn_id):
            await self._terminalize(conversation_ref, turn_id, TurnState.ABORTED, AbortReason.EXECUTION_FAILURE)

    async def _converge_turn_from_response_terminal(
        self, conversation_ref: ConversationRef, turn_id: TurnId, response: StoredResponse
    ) -> None:
        consequence = consequence_from_response_terminal(response.state, response.abort_reason)
        turn = await self._store.get_turn(conversation_ref, turn_id)
        if turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}:
            return
        await self._store.terminalize_turn(
            self._require_token(), conversation_ref, turn_id, turn.revision,
            consequence.state, consequence.abort_reason,
        )

    async def _turn_is_terminal(self, conversation_ref: ConversationRef, turn_id: TurnId) -> bool:
        turn = await self._store.get_turn(conversation_ref, turn_id)
        return turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}

    async def _abort_with_retry(
        self, token: CoreAuthorityToken, response: StoredResponse, reason: ResponseAbortReason, caller_user_id: UserId
    ) -> StoredResponse:
        while True:
            try:
                return await self._store.abort_response(token, response.response_id, response.revision, reason)
            except CoreStateError as error:
                if error.code != "RESPONSE_REVISION_CONFLICT":
                    raise
                response = await self._store.get_authorized_response(response.response_id, caller_user_id)

    async def _terminalize(
        self, conversation_ref: ConversationRef, turn_id: TurnId, state: TurnState, abort_reason: Optional[AbortReason] = None
    ) -> None:
        turn = await self._store.get_turn(conversation_ref, turn_id)
        await self._store.terminalize_turn(
            self._require_token(), conversation_ref, turn.turn_ref.turn_id, turn.revision, state, abort_reason
        )

    def _require_token(self) -> CoreAuthorityToken:
        if self._token is None:
            raise RuntimeError("Core has not acquired authority")
        return self._token

    @staticmethod
    def _scheduler_key(conversation_ref: ConversationRef, turn_id: TurnId) -> str:
        return conversation_ref.conversation_id.value + ":" + turn_id.value
