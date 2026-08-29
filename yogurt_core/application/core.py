"""Application facade for the R2-A process-lifetime reference Core."""

import uuid
from typing import AsyncIterator, Optional

from yogurt_contracts.conversation.refs import ConversationRef
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import CoreInstanceId, ExecutionAttemptId, ResponseId, SegmentId, TurnId, UserId
from yogurt_contracts.identity.principal import ExternalPrincipal
from yogurt_contracts.response.events import ResponseEvent
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken

from yogurt_core.application.commands import GetResponseCommand, GetTurnCommand, SubmitTurnCommand, WatchResponseCommand
from yogurt_core.application.results import TurnAdmissionResult, TurnAdmissionStatus
from yogurt_core.authority.conversation_authority import ConversationAuthority
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.context.projection import ContextProjectionService
from yogurt_core.model.events import AttemptTerminal, AttemptTerminalKind, VisibleTextDelta
from yogurt_core.model.execution import ExecutionAttemptSnapshot, StoredResponse, StoredTurn
from yogurt_core.ports.identity import IdentityResolver
from yogurt_core.ports.model import ModelPort
from yogurt_core.ports.persona import PersonaSnapshotPort
from yogurt_core.ports.state_store import CoreStateError, CoreStateStore
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
        await ConversationAuthority(self._store).create(conversation_ref)

    async def submit_turn(self, command: SubmitTurnCommand) -> TurnAdmissionResult:
        token = self._require_token()
        resolution = await self._identity_resolver.resolve(command.external_principal)
        # The authenticated caller and resolved identity must both own the ref before
        # the store receives an InputCommitted admission request.
        if resolution.logical_user != command.caller_context.logical_user_id:
            return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="CALLER_IDENTITY_MISMATCH")
        authority = ConversationAuthority(self._store)
        if not await authority.validate_owner(command.conversation_ref, resolution.logical_user):
            return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="CONVERSATION_NOT_OWNED")
        persona = await self._persona_snapshots.get_snapshot(command.conversation_ref.persona_id)
        snapshot = TurnSnapshot(
            identity_revision=resolution.identity_revision,
            core_config_revision=command.core_config_revision,
            turn_origin=command.input.origin,
            conversation_context_revision=command.conversation_context_revision,
            principal_binding_reference=command.principal_binding_reference,
            persona_snapshot=persona,
        )
        result = await self._store.try_admit_turn(
            token, command.conversation_ref, command.turn_id, command.fingerprint, snapshot, command.input
        )
        if result.status == TurnAdmissionStatus.NEWLY_ADMITTED:
            self._scheduler.schedule(self._scheduler_key(command.conversation_ref, command.turn_id), self._run_turn(command.conversation_ref, command.turn_id))
        return result

    async def get_turn(self, command: GetTurnCommand) -> StoredTurn:
        return await self._store.get_authorized_turn(
            command.conversation_ref, command.turn_id, command.caller_context.logical_user_id
        )

    async def get_response(self, command: GetResponseCommand) -> StoredResponse:
        return await self._store.get_authorized_response(command.response_id, command.caller_context.logical_user_id)

    async def watch_response(self, command: WatchResponseCommand) -> AsyncIterator[ResponseEvent]:
        # Authorize before opening the stream; IDs alone confer no read capability.
        await self._store.get_authorized_response(command.response_id, command.caller_context.logical_user_id)
        async for event in self._store.watch_response_events(command.response_id, command.after_event_sequence):
            yield event

    async def cancel_response(self, response_id: ResponseId, caller_user_id: UserId, reason: str = "CANCELLED") -> None:
        """Authoritative close first; a provider ignoring cancel cannot commit late output."""
        token = self._require_token()
        response = await self._store.get_authorized_response(response_id, caller_user_id)
        # A segment may win its CAS immediately before cancellation. Refreshing and
        # closing then preserves that prefix while cancellation still terminalizes.
        while True:
            try:
                closed = await self._store.abort_response(token, response_id, response.revision, reason)
                break
            except CoreStateError as error:
                if error.code != "RESPONSE_REVISION_CONFLICT":
                    raise
                response = await self._store.get_authorized_response(response_id, caller_user_id)
        turn = await self._store.get_turn_by_response(response_id)
        await self._store.terminalize_turn(token, turn.conversation_ref, turn.turn_ref.turn_id, turn.revision, reason)
        if closed.attempts:
            await self._model.cancel(closed.attempts[-1])

    async def drain(self) -> None:
        await self._scheduler.drain()

    async def _run_turn(self, conversation_ref: ConversationRef, turn_id: TurnId) -> None:
        try:
            token = self._require_token()
            turn = await self._store.get_turn(conversation_ref, turn_id)
            response_id = ResponseId(value="response-" + uuid.uuid4().hex)
            response = await self._store.commit_response_started(token, conversation_ref, turn.turn_ref.turn_id, response_id)
            projection = await self._projection.project(turn)
            context = self._context_policy.compose(projection, turn)
            saw_committed_prefix = False
            for number in (1, 2):
                attempt = ExecutionAttemptSnapshot(
                    execution_attempt_id=ExecutionAttemptId(value="attempt-" + uuid.uuid4().hex),
                    response_id=response_id,
                    attempt_number=number,
                    model_binding="r2a-fake-model",
                    provider_generation="r2a",
                    provider_config_revision="r2a",
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
                    await self._terminalize(conversation_ref, turn.turn_ref.turn_id, "COMPLETED")
                    return
                # A fallback is allowed only before the first semantic segment commits.
                if saw_committed_prefix or number == 2:
                    await self._store.abort_response(token, response_id, response.revision, terminal.reason or "PROVIDER_FAILURE")
                    await self._terminalize(conversation_ref, turn.turn_ref.turn_id, terminal.reason or "PROVIDER_FAILURE")
                    return
        except CoreStateError as error:
            # Cancellation and authority transfer deliberately fence a late task.
            if error.code in {"STALE_CORE_AUTHORITY", "RESPONSE_NOT_OPEN", "TURN_NOT_OPEN"}:
                return
            raise

    async def _terminalize(self, conversation_ref: ConversationRef, turn_id: TurnId, reason: str) -> None:
        turn = await self._store.get_turn(conversation_ref, turn_id)
        await self._store.terminalize_turn(self._require_token(), conversation_ref, turn.turn_ref.turn_id, turn.revision, reason)

    def _require_token(self) -> CoreAuthorityToken:
        if self._token is None:
            raise RuntimeError("Core has not acquired authority")
        return self._token

    @staticmethod
    def _scheduler_key(conversation_ref: ConversationRef, turn_id: TurnId) -> str:
        return conversation_ref.conversation_id.value + ":" + turn_id.value
