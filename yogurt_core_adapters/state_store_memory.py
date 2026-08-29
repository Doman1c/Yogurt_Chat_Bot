"""Authoritative in-memory state machine for the R2-A reference runtime.

It deliberately offers no persistence or recovery guarantee: process loss loses all
state.  The lock below defines the linearization point for every mutation.
"""

import asyncio
import uuid
from dataclasses import replace
from typing import AsyncIterator, Dict, List, Optional, Tuple

from yogurt_contracts.conversation.input import TurnInput
from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import (
    CoreInstanceId,
    ResponseEventId,
    ResponseId,
    SegmentId,
    StoreAuthorityId,
    TurnId,
    UserId,
)
from yogurt_contracts.response.events import (
    ResponseAbortedEvent,
    ResponseEvent,
    ResponseFinalizedEvent,
    ResponseStartedEvent,
    SegmentCommittedEvent,
)
from yogurt_contracts.response.lifecycle import ResponseAbortReason, ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken
from yogurt_contracts.foundation.durability import CorePersistenceMode

from yogurt_core.application.results import AdmissionResponseState, TurnAdmissionResult, TurnAdmissionStatus
from yogurt_core.context.models import ContextEntry, ContextRole
from yogurt_core.model.execution import StoredResponse, StoredTurn
from yogurt_core.ports.state_store import CoreStateError


def _conversation_key(ref: ConversationRef) -> str:
    return "|".join((ref.persona_id.value, ref.user_id.value, ref.conversation_id.value))


def _turn_key(ref: ConversationRef, turn_id: TurnId) -> str:
    return _conversation_key(ref) + "|" + turn_id.value


class InMemoryCoreStateStore:
    """Single authoritative state adapter, intentionally ephemeral only."""

    persistence_mode = CorePersistenceMode.EPHEMERAL

    def __init__(self, store_authority_id: Optional[StoreAuthorityId] = None) -> None:
        self._condition = asyncio.Condition()
        self._store_authority_id = store_authority_id or StoreAuthorityId(value="memory-store-" + uuid.uuid4().hex)
        self._authority_epoch = 0
        self._authority: Optional[CoreAuthorityToken] = None
        self._conversations: Dict[str, ConversationRef] = {}
        self._turns: Dict[str, StoredTurn] = {}
        self._claims: Dict[str, str] = {}
        self._responses: Dict[str, StoredResponse] = {}
        self._response_turns: Dict[str, str] = {}

    async def acquire_authority(self, core_instance_id: CoreInstanceId) -> CoreAuthorityToken:
        async with self._condition:
            if self._authority is not None:
                raise CoreStateError("CORE_AUTHORITY_ALREADY_OWNED")
            self._authority_epoch += 1
            self._authority = CoreAuthorityToken(
                core_instance_id=core_instance_id,
                store_authority_id=self._store_authority_id,
                authority_epoch=self._authority_epoch,
                token_nonce=uuid.uuid4().hex,
            )
            return self._authority

    async def release_authority(self, token: CoreAuthorityToken) -> None:
        async with self._condition:
            self._validate_token(token)
            self._authority = None
            self._authority_epoch += 1

    async def create_conversation(self, token: CoreAuthorityToken, conversation_ref: ConversationRef) -> None:
        async with self._condition:
            self._validate_token(token)
            key = _conversation_key(conversation_ref)
            existing = self._conversations.get(key)
            if existing is not None and existing != conversation_ref:
                raise CoreStateError("CONVERSATION_CONFLICT")
            self._conversations[key] = conversation_ref

    async def resolve_conversation(self, conversation_ref: ConversationRef) -> ConversationRef:
        async with self._condition:
            found = self._conversations.get(_conversation_key(conversation_ref))
            if found is None:
                raise CoreStateError("CONVERSATION_NOT_FOUND")
            return found

    async def validate_conversation_owner(self, conversation_ref: ConversationRef, user_id: UserId) -> bool:
        async with self._condition:
            found = self._conversations.get(_conversation_key(conversation_ref))
            return found is not None and found.user_id == user_id

    async def try_admit_turn(
        self,
        token: CoreAuthorityToken,
        conversation_ref: ConversationRef,
        turn_id: TurnId,
        fingerprint: object,
        snapshot: TurnSnapshot,
        turn_input: TurnInput,
    ) -> TurnAdmissionResult:
        """Atomic claim + idempotency + snapshot + InputCommitted boundary."""
        async with self._condition:
            self._validate_token(token)
            conversation_key = _conversation_key(conversation_ref)
            if self._conversations.get(conversation_key) != conversation_ref:
                return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="CONVERSATION_NOT_FOUND")
            key = _turn_key(conversation_ref, turn_id)
            existing = self._turns.get(key)
            # TurnFingerprint is a structured R1 contract, so use its canonical
            # JSON representation rather than an incidental repr.
            fingerprint_value = getattr(fingerprint, "model_dump_json", lambda: str(fingerprint))()
            if existing is not None:
                if existing.fingerprint != fingerprint_value:
                    return TurnAdmissionResult(status=TurnAdmissionStatus.TURN_ID_CONFLICT, turn_id=turn_id)
                return self._existing_admission(existing)
            active = self._claims.get(conversation_key)
            if active is not None:
                return TurnAdmissionResult(status=TurnAdmissionStatus.CONVERSATION_BUSY, reason="CONVERSATION_BUSY")
            turn = StoredTurn(
                conversation_ref=conversation_ref,
                turn_ref=TurnRef(conversation_id=conversation_ref.conversation_id, turn_id=turn_id),
                fingerprint=fingerprint_value,
                snapshot=snapshot,
                turn_input=turn_input,
            )
            self._claims[conversation_key] = key
            self._turns[key] = turn
            return TurnAdmissionResult(status=TurnAdmissionStatus.NEWLY_ADMITTED, turn_id=turn_id)

    async def get_turn(self, conversation_ref: ConversationRef, turn_id: TurnId) -> StoredTurn:
        async with self._condition:
            found = self._turns.get(_turn_key(conversation_ref, turn_id))
            if found is None:
                raise CoreStateError("TURN_NOT_FOUND")
            return found

    async def get_response(self, response_id: ResponseId) -> StoredResponse:
        async with self._condition:
            found = self._responses.get(response_id.value)
            if found is None:
                raise CoreStateError("RESPONSE_NOT_FOUND")
            return found

    async def get_turn_by_response(self, response_id: ResponseId) -> StoredTurn:
        async with self._condition:
            key = self._response_turns.get(response_id.value)
            if key is None:
                raise CoreStateError("RESPONSE_NOT_FOUND")
            return self._turns[key]

    async def get_authorized_turn(self, conversation_ref: ConversationRef, turn_id: TurnId, caller: UserId) -> StoredTurn:
        async with self._condition:
            self._validate_conversation_read(conversation_ref, caller)
            found = self._turns.get(_turn_key(conversation_ref, turn_id))
            if found is None:
                raise CoreStateError("TURN_NOT_FOUND")
            return found

    async def get_authorized_response(self, response_id: ResponseId, caller: UserId) -> StoredResponse:
        async with self._condition:
            response = self._responses.get(response_id.value)
            if response is None:
                raise CoreStateError("RESPONSE_NOT_FOUND")
            turn = self._turns[self._response_turns[response_id.value]]
            self._validate_conversation_read(turn.conversation_ref, caller)
            return response

    async def commit_response_started(
        self, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, response_id: ResponseId
    ) -> StoredResponse:
        async with self._condition:
            self._validate_token(token)
            key = _turn_key(conversation_ref, turn_id)
            turn = self._turns.get(key)
            if turn is None:
                raise CoreStateError("TURN_NOT_FOUND")
            if turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}:
                raise CoreStateError("TURN_NOT_OPEN")
            if turn.response_id is not None:
                return self._responses[turn.response_id.value]
            event = ResponseStartedEvent(
                event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response_id, event_sequence=1
            )
            response = StoredResponse(response_id=response_id, turn_key=key, state=ResponseState.STARTED, revision=1, events=(event,))
            self._responses[response_id.value] = response
            self._response_turns[response_id.value] = key
            self._turns[key] = replace(turn, response_id=response_id, revision=turn.revision + 1)
            self._condition.notify_all()
            return response

    async def add_execution_attempt(self, token: CoreAuthorityToken, response_id: ResponseId, attempt: ExecutionAttemptSnapshot) -> StoredResponse:
        async with self._condition:
            self._validate_token(token)
            response = self._require_open_response(response_id)
            if attempt.response_id != response_id:
                raise CoreStateError("ATTEMPT_RESPONSE_MISMATCH")
            response = replace(response, attempts=response.attempts + (attempt,), revision=response.revision + 1)
            self._responses[response_id.value] = response
            return response

    async def commit_segment(
        self,
        token: CoreAuthorityToken,
        response_id: ResponseId,
        expected_response_revision: int,
        segment: AgentOutputSegmentCommitted,
    ) -> StoredResponse:
        async with self._condition:
            self._validate_token(token)
            response = self._require_open_response(response_id)
            if response.revision != expected_response_revision:
                # Retrying an already successful segment is deliberately idempotent.
                for present in response.segments:
                    if present.segment_id == segment.segment_id:
                        if present == segment:
                            return response
                        raise CoreStateError("SEGMENT_ID_CONFLICT")
                raise CoreStateError("RESPONSE_REVISION_CONFLICT")
            if segment.response_id != response_id:
                raise CoreStateError("SEGMENT_RESPONSE_MISMATCH")
            if response.segments and segment.sequence <= response.segments[-1].sequence:
                raise CoreStateError("SEGMENT_SEQUENCE_CONFLICT")
            event = SegmentCommittedEvent(
                event_id=ResponseEventId(value="event-" + uuid.uuid4().hex),
                response_id=response_id,
                event_sequence=len(response.events) + 1,
                segment_id=segment.segment_id,
                segment_sequence=segment.sequence,
                text=segment.text,
                language=segment.language,
            )
            response = replace(
                response, segments=response.segments + (segment,), events=response.events + (event,), revision=response.revision + 1
            )
            self._responses[response_id.value] = response
            self._condition.notify_all()
            return response

    async def finalize_response(self, token: CoreAuthorityToken, response_id: ResponseId, expected_response_revision: int) -> StoredResponse:
        async with self._condition:
            self._validate_token(token)
            response = self._require_open_response(response_id)
            if response.revision != expected_response_revision:
                raise CoreStateError("RESPONSE_REVISION_CONFLICT")
            event = ResponseFinalizedEvent(
                event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response_id,
                event_sequence=len(response.events) + 1,
            )
            response = replace(response, state=ResponseState.FINALIZED, events=response.events + (event,), revision=response.revision + 1)
            self._responses[response_id.value] = response
            self._condition.notify_all()
            return response

    async def abort_response(
        self, token: CoreAuthorityToken, response_id: ResponseId, expected_response_revision: int, reason: ResponseAbortReason
    ) -> StoredResponse:
        async with self._condition:
            self._validate_token(token)
            response = self._require_open_response(response_id)
            if response.revision != expected_response_revision:
                raise CoreStateError("RESPONSE_REVISION_CONFLICT")
            event = ResponseAbortedEvent(
                event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response_id,
                event_sequence=len(response.events) + 1, reason=reason,
            )
            response = replace(response, state=ResponseState.ABORTED, events=response.events + (event,), revision=response.revision + 1)
            self._responses[response_id.value] = response
            self._condition.notify_all()
            return response

    async def terminalize_turn(
        self,
        token: CoreAuthorityToken,
        conversation_ref: ConversationRef,
        turn_id: TurnId,
        expected_turn_revision: int,
        state: TurnState,
        abort_reason: Optional[AbortReason] = None,
    ) -> StoredTurn:
        """Atomic terminal event/claim release transition; never split by application code."""
        async with self._condition:
            self._validate_token(token)
            key = _turn_key(conversation_ref, turn_id)
            turn = self._turns.get(key)
            if turn is None:
                raise CoreStateError("TURN_NOT_FOUND")
            if turn.revision != expected_turn_revision:
                raise CoreStateError("TURN_REVISION_CONFLICT")
            if turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}:
                return turn
            conversation_key = _conversation_key(conversation_ref)
            if self._claims.get(conversation_key) != key:
                raise CoreStateError("CONVERSATION_CLAIM_MISMATCH")
            if state not in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}:
                raise CoreStateError("INVALID_TURN_TERMINAL_STATE")
            if state == TurnState.ABORTED and abort_reason is None:
                raise CoreStateError("ABORT_REASON_REQUIRED")
            if state != TurnState.ABORTED and abort_reason is not None:
                raise CoreStateError("UNEXPECTED_ABORT_REASON")
            # All validation happens before either state write. This is the
            # in-memory transaction boundary for terminalization + claim release.
            turn = replace(turn, state=state, abort_reason=abort_reason, revision=turn.revision + 1)
            self._turns[key] = turn
            del self._claims[conversation_key]
            return turn

    async def get_conversation_context(self, conversation_ref: ConversationRef, excluding_turn_id: TurnId) -> Tuple[ContextEntry, ...]:
        async with self._condition:
            entries: List[ContextEntry] = []
            for turn in self._turns.values():
                if turn.conversation_ref != conversation_ref or turn.turn_ref.turn_id == excluding_turn_id:
                    continue
                entries.extend(
                    ContextEntry(role=ContextRole.USER, text=part.text)
                    for part in turn.turn_input.parts if hasattr(part, "text")
                )
                if turn.response_id is not None:
                    response = self._responses.get(turn.response_id.value)
                    if response is not None:
                        entries.extend(ContextEntry(role=ContextRole.ASSISTANT, text=segment.text) for segment in response.segments)
            return tuple(entries)

    async def watch_response_events(self, response_id: ResponseId, after_event_sequence: int) -> AsyncIterator[ResponseEvent]:
        cursor = after_event_sequence
        while True:
            async with self._condition:
                response = self._responses.get(response_id.value)
                if response is None:
                    raise CoreStateError("RESPONSE_NOT_FOUND")
                replay = tuple(event for event in response.events if event.event_sequence > cursor)
                if not replay:
                    if response.state in {ResponseState.FINALIZED, ResponseState.ABORTED}:
                        return
                    # The predicate is checked while holding the same lock as commits:
                    # no replay->subscribe gap can lose a committed event.
                    await self._condition.wait_for(
                        lambda: response_id.value in self._responses
                        and any(event.event_sequence > cursor for event in self._responses[response_id.value].events)
                    )
                    continue
            for event in replay:
                cursor = event.event_sequence
                yield event
            if replay and isinstance(replay[-1], (ResponseFinalizedEvent, ResponseAbortedEvent)):
                return

    async def active_claim(self, conversation_ref: ConversationRef) -> Optional[TurnId]:
        async with self._condition:
            key = self._claims.get(_conversation_key(conversation_ref))
            return None if key is None else self._turns[key].turn_ref.turn_id

    def _validate_token(self, token: CoreAuthorityToken) -> None:
        if self._authority != token:
            raise CoreStateError("STALE_CORE_AUTHORITY")
        if token is None:
            raise CoreStateError("STALE_CORE_AUTHORITY")
        if token.store_authority_id != self._store_authority_id or token.authority_epoch != self._authority_epoch:
            raise CoreStateError("STALE_CORE_AUTHORITY")

    def _validate_conversation_read(self, conversation_ref: ConversationRef, caller: UserId) -> None:
        found = self._conversations.get(_conversation_key(conversation_ref))
        if found is None or found.user_id != caller:
            raise CoreStateError("READ_NOT_AUTHORIZED")

    def _require_open_response(self, response_id: ResponseId) -> StoredResponse:
        response = self._responses.get(response_id.value)
        if response is None:
            raise CoreStateError("RESPONSE_NOT_FOUND")
        if response.state != ResponseState.STARTED:
            raise CoreStateError("RESPONSE_NOT_OPEN")
        turn = self._turns[self._response_turns[response_id.value]]
        if turn.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}:
            raise CoreStateError("TURN_NOT_OPEN")
        return response

    def _existing_admission(self, turn: StoredTurn) -> TurnAdmissionResult:
        if turn.response_id is None:
            return TurnAdmissionResult(status=TurnAdmissionStatus.SAME_LOGICAL_TURN, turn_id=turn.turn_ref.turn_id)
        response = self._responses[turn.response_id.value]
        mapping = {
            ResponseState.STARTED: AdmissionResponseState.STARTED,
            ResponseState.FINALIZED: AdmissionResponseState.FINALIZED,
            ResponseState.ABORTED: AdmissionResponseState.ABORTED,
        }
        return TurnAdmissionResult(
            status=TurnAdmissionStatus.SAME_LOGICAL_TURN,
            turn_id=turn.turn_ref.turn_id,
            response_id=turn.response_id,
            response_state=mapping[response.state],
        )
