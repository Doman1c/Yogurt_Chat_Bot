"""SQLite-backed authoritative R2-B Core state store.

This adapter has a deliberately small persistence surface: append-only facts are
the durable source of truth and the relational tables are rebuildable read
projections.  SQLite transactions never await and never invoke providers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Set, Tuple, TypeVar, Union, cast

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from yogurt_contracts.conversation.fingerprint import TurnFingerprint
from yogurt_contracts.conversation.input import TurnInput
from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState, validate_turn_transition
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.durability import CorePersistenceMode
from yogurt_contracts.foundation.facts import CommitCertainty, FactCommitResult, FactEnvelope, utc_now
from yogurt_contracts.foundation.identifiers import CoreInstanceId, FactId, OperationAttemptId, ResponseEventId, ResponseId, StoreAuthorityId, TurnId, UserId
from yogurt_contracts.response.events import ResponseAbortedEvent, ResponseEvent, ResponseFinalizedEvent, ResponseStartedEvent, SegmentCommittedEvent
from yogurt_contracts.response.lifecycle import ResponseAbortReason, ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken
from yogurt_contracts.operations.intent import OperationIntentCommitted
from yogurt_contracts.foundation.identifiers import OperationId
from yogurt_contracts.memory.causal import MemoryCausalLaneSnapshot, MemoryCausalOperation, MemoryCausalResolution, MemoryDispatchAttempt, MemoryObservationOperation, MemoryReconciliationProof
from yogurt_contracts.operations.attempts import DispatchAttempt

from yogurt_core.application.results import AdmissionResponseState, TurnAdmissionResult, TurnAdmissionStatus
from yogurt_core.context.models import ContextEntry, ContextRole
from yogurt_core.model.execution import StoredResponse, StoredTurn
from yogurt_core.ports.state_store import CoreStateError, StateCommit
from yogurt_core.operations.models import DurableOperationState, NotDispatchedReason, StoredOperation
from yogurt_core.memory.causal_lane import MemoryCausalLane, MemoryCausalLaneError


_SCHEMA_VERSION = "1"
_TERMINAL_TURNS = {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.ABORTED}
_TERMINAL_RESPONSES = {ResponseState.FINALIZED, ResponseState.ABORTED}
_WriteResult = TypeVar("_WriteResult")
_LOCAL_WRITER_LOCKS: Set[str] = set()


def _conversation_key(ref: ConversationRef) -> str:
    return "|".join((ref.persona_id.value, ref.user_id.value, ref.conversation_id.value))


def _turn_key(ref: ConversationRef, turn_id: TurnId) -> str:
    return _conversation_key(ref) + "|" + turn_id.value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str) -> Any:
    return json.loads(value)


def _parse_datetime(value: object) -> datetime:
    """Accept the RFC3339 ``Z`` spelling on all supported Python versions."""

    text = str(value)
    return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)


def _contract(value: Any) -> Dict[str, Any]:
    """Map contract values to a named JSON shape, never a model JSON blob."""

    return cast(Dict[str, Any], value.model_dump(mode="json"))


def _fact_id(domain_key: str) -> FactId:
    return FactId(value="fact-" + hashlib.sha256(domain_key.encode("utf-8")).hexdigest())


class SQLiteCoreStateStore:
    """Local-filesystem, single-writer, rebuildable durable Core store."""

    persistence_mode = CorePersistenceMode.DURABLE

    def __init__(self, database_path: Union[str, Path], store_authority_id: Optional[StoreAuthorityId] = None) -> None:
        self._path = str(database_path)
        self._lock = asyncio.Lock()
        self._unknown_commit_once = False
        self._semantic_unknown_once = False
        self._semantic_not_committed_once = False
        self._writer_lock: Optional[Any] = None
        self._serving = True
        try:
            self._connection = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False, timeout=2.0)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            if self._path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = FULL")
            self._initialize(store_authority_id)
        except (CoreStateError, sqlite3.DatabaseError, sqlite3.OperationalError):
            # Never reset or replace a failed store: it is explicitly NOT_SERVING.
            self._serving = False
            self._connection = None  # type: ignore[assignment]

    @property
    def is_serving(self) -> bool:
        return self._serving

    def simulate_outcome_unknown_once(self) -> None:
        """Test seam: commit succeeds but the caller cannot learn that it did."""

        self._unknown_commit_once = True

    def simulate_semantic_outcome_unknown_once(self) -> None:
        self._semantic_unknown_once = True

    def simulate_semantic_not_committed_once(self) -> None:
        self._semantic_not_committed_once = True

    async def close(self) -> None:
        async with self._lock:
            self._release_writer_lock()
            if self._connection is not None:
                self._connection.close()
                self._connection = None  # type: ignore[assignment]
            self._serving = False

    async def acquire_authority(self, core_instance_id: CoreInstanceId) -> CoreAuthorityToken:
        async with self._lock:
            self._acquire_writer_lock()
            try:
                return self._acquire_authority(core_instance_id)
            except Exception:
                self._release_writer_lock()
                raise

    async def release_authority(self, token: CoreAuthorityToken) -> None:
        async with self._lock:
            def write(connection: sqlite3.Connection) -> None:
                self._validate_token(connection, token)
                epoch = int(self._meta(connection, "authority_epoch")) + 1
                self._set_meta(connection, "authority_epoch", str(epoch))
                self._set_meta(connection, "active_authority", "")

            self._write(write)
            self._release_writer_lock()

    async def recover(self, token: CoreAuthorityToken) -> None:
        """Rebuild local projections then deterministically close old work.

        No model work is resumed after a process loss.  Each repair is recorded
        in the same SQLite transaction as the resulting projection and claim
        release, making a crash during recovery safely repeatable.
        """
        async with self._lock:
            def write(connection: sqlite3.Connection) -> None:
                self._validate_token(connection, token)
                facts = connection.execute("SELECT fact_id, fact_type, fact_schema_version, aggregate_id, aggregate_revision, recorded_at, payload FROM facts ORDER BY rowid").fetchall()
                connection.execute("DELETE FROM claims")
                connection.execute("DELETE FROM responses")
                connection.execute("DELETE FROM turns")
                connection.execute("DELETE FROM conversations")
                connection.execute("DELETE FROM operations")
                connection.execute("DELETE FROM memory_causal_lanes")
                connection.execute("DELETE FROM memory_observation_operations")
                connection.execute("DELETE FROM memory_dispatch_attempts")
                for row in facts:
                    self._apply_projection_fact(connection, self._decode_fact(row))
                rows = connection.execute("SELECT turn_key, payload FROM turns").fetchall()
                for row in rows:
                    key = str(row["turn_key"])
                    turn = self._decode_turn(_load_json(str(row["payload"])))
                    if turn.state in _TERMINAL_TURNS:
                        continue
                    if turn.response_id is None:
                        repaired = replace(turn, state=TurnState.ABORTED, abort_reason=AbortReason.RECOVERY, revision=turn.revision + 1)
                        self._append_fact(connection, self._projection_fact("turn_recovered", key, repaired.revision, {"kind": "turn", "turn": self._encode_turn(repaired)}))
                        self._write_turn(connection, key, repaired)
                        continue
                    response = self._response_from_connection(connection, turn.response_id)
                    if response.state == ResponseState.STARTED:
                        event = ResponseAbortedEvent(event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response.response_id, event_sequence=len(response.events) + 1, reason=ResponseAbortReason.RECOVERY)
                        response = replace(response, state=ResponseState.ABORTED, abort_reason=ResponseAbortReason.RECOVERY, events=response.events + (event,), revision=response.revision + 1)
                        self._append_fact(connection, self._projection_fact("response_recovered", response.response_id.value, response.revision, {"kind": "response", "response": self._encode_response(response)}))
                        self._write_response(connection, response)
                    if response.state == ResponseState.FINALIZED:
                        target, reason = TurnState.COMPLETED, None
                    elif response.abort_reason == ResponseAbortReason.CANCELLED:
                        target, reason = TurnState.CANCELLED, None
                    else:
                        if response.abort_reason is None:
                            raise CoreStateError("INCOMPATIBLE_FACT_SCHEMA")
                        target, reason = TurnState.ABORTED, AbortReason(response.abort_reason.value)
                    repaired = replace(turn, state=target, abort_reason=reason, revision=turn.revision + 1)
                    self._append_fact(connection, self._projection_fact("turn_recovered", key, repaired.revision, {"kind": "turn", "turn": self._encode_turn(repaired)}))
                    self._write_turn(connection, key, repaired)
                # Recovery never leaves a process-lost active claim behind.
                connection.execute("DELETE FROM claims")

            try:
                self._write(write)
            except CoreStateError as error:
                if error.code in {"INCOMPATIBLE_FACT_SCHEMA", "INCOMPATIBLE_SCHEMA"}:
                    self._serving = False
                raise

    async def create_conversation(self, token: CoreAuthorityToken, conversation_ref: ConversationRef) -> None:
        async with self._lock:
            def write(connection: sqlite3.Connection) -> None:
                self._validate_token(connection, token)
                key = _conversation_key(conversation_ref)
                row = connection.execute("SELECT payload FROM conversations WHERE conversation_key = ?", (key,)).fetchone()
                if row is not None:
                    if _load_json(str(row["payload"])) != _contract(conversation_ref):
                        raise CoreStateError("CONVERSATION_CONFLICT")
                    return
                self._append_fact(connection, self._projection_fact("conversation_created", key, 1, {"kind": "conversation", "conversation": _contract(conversation_ref)}))
                connection.execute("INSERT INTO conversations(conversation_key, payload) VALUES (?, ?)", (key, _json(_contract(conversation_ref))))

            self._write(write)

    async def resolve_conversation(self, conversation_ref: ConversationRef) -> ConversationRef:
        async with self._lock:
            row = self._read_one("SELECT payload FROM conversations WHERE conversation_key = ?", (_conversation_key(conversation_ref),), "CONVERSATION_NOT_FOUND")
            return ConversationRef.model_validate(_load_json(str(row["payload"])))

    async def validate_conversation_owner(self, conversation_ref: ConversationRef, user_id: UserId) -> bool:
        async with self._lock:
            self._require_serving()
            row = self._connection.execute("SELECT payload FROM conversations WHERE conversation_key = ?", (_conversation_key(conversation_ref),)).fetchone()
            return row is not None and ConversationRef.model_validate(_load_json(str(row["payload"]))).user_id == user_id

    async def try_admit_turn(self, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, fingerprint: TurnFingerprint, snapshot: TurnSnapshot, turn_input: TurnInput) -> TurnAdmissionResult:
        async with self._lock:
            return self._write(lambda connection: self._try_admit_turn_tx(connection, token, conversation_ref, turn_id, fingerprint, snapshot, turn_input))

    async def try_admit_turn_commit(self, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, fingerprint: TurnFingerprint, snapshot: TurnSnapshot, turn_input: TurnInput) -> StateCommit[TurnAdmissionResult]:
        identity = "admission:" + _turn_key(conversation_ref, turn_id) + ":" + fingerprint.model_dump_json()
        async with self._lock:
            return self._write_commit(identity, lambda connection: self._try_admit_turn_tx(connection, token, conversation_ref, turn_id, fingerprint, snapshot, turn_input), lambda: self._reconcile_admission(conversation_ref, turn_id, fingerprint))

    async def get_turn(self, conversation_ref: ConversationRef, turn_id: TurnId) -> StoredTurn:
        async with self._lock:
            row = self._read_one("SELECT payload FROM turns WHERE turn_key = ?", (_turn_key(conversation_ref, turn_id),), "TURN_NOT_FOUND")
            return self._decode_turn(_load_json(str(row["payload"])))

    async def get_response(self, response_id: ResponseId) -> StoredResponse:
        async with self._lock:
            row = self._read_one("SELECT payload FROM responses WHERE response_id = ?", (response_id.value,), "RESPONSE_NOT_FOUND")
            return self._decode_response(_load_json(str(row["payload"])))

    async def get_turn_by_response(self, response_id: ResponseId) -> StoredTurn:
        async with self._lock:
            row = self._read_one("SELECT turn_key FROM responses WHERE response_id = ?", (response_id.value,), "RESPONSE_NOT_FOUND")
            turn_row = self._read_one("SELECT payload FROM turns WHERE turn_key = ?", (str(row["turn_key"]),), "TURN_NOT_FOUND")
            return self._decode_turn(_load_json(str(turn_row["payload"])))

    async def get_authorized_turn(self, conversation_ref: ConversationRef, turn_id: TurnId, caller: UserId) -> StoredTurn:
        async with self._lock:
            self._validate_conversation_read(conversation_ref, caller)
            row = self._read_one("SELECT payload FROM turns WHERE turn_key = ?", (_turn_key(conversation_ref, turn_id),), "TURN_NOT_FOUND")
            return self._decode_turn(_load_json(str(row["payload"])))

    async def get_authorized_response(self, response_id: ResponseId, caller: UserId) -> StoredResponse:
        async with self._lock:
            row = self._read_one("SELECT turn_key, payload FROM responses WHERE response_id = ?", (response_id.value,), "RESPONSE_NOT_FOUND")
            turn_row = self._read_one("SELECT payload FROM turns WHERE turn_key = ?", (str(row["turn_key"]),), "TURN_NOT_FOUND")
            turn = self._decode_turn(_load_json(str(turn_row["payload"])))
            self._validate_conversation_read(turn.conversation_ref, caller)
            return self._decode_response(_load_json(str(row["payload"])))

    async def commit_response_started(self, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, response_id: ResponseId) -> StoredResponse:
        async with self._lock:
            def write(connection: sqlite3.Connection) -> StoredResponse:
                self._validate_token(connection, token)
                key = _turn_key(conversation_ref, turn_id)
                turn = self._turn_from_connection(connection, key)
                if turn.state in _TERMINAL_TURNS:
                    raise CoreStateError("TURN_NOT_OPEN")
                if turn.response_id is not None:
                    return self._response_from_connection(connection, turn.response_id)
                if not validate_turn_transition(turn.state, TurnState.PROCESSING):
                    raise CoreStateError("INVALID_TURN_TRANSITION")
                event = ResponseStartedEvent(event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response_id, event_sequence=1)
                response = StoredResponse(response_id=response_id, turn_key=key, state=ResponseState.STARTED, revision=1, events=(event,))
                turn = replace(turn, response_id=response_id, state=TurnState.PROCESSING, revision=turn.revision + 1)
                self._append_fact(connection, self._projection_fact("response_started", response_id.value, response.revision, {"kind": "response", "response": self._encode_response(response)}))
                self._append_fact(connection, self._projection_fact("turn_processing", key, turn.revision, {"kind": "turn", "turn": self._encode_turn(turn)}))
                connection.execute("INSERT INTO responses(response_id, turn_key, payload) VALUES (?, ?, ?)", (response_id.value, key, _json(self._encode_response(response))))
                self._write_turn(connection, key, turn)
                return response

            return self._write(write)

    async def add_execution_attempt(self, token: CoreAuthorityToken, response_id: ResponseId, attempt: ExecutionAttemptSnapshot) -> StoredResponse:
        async with self._lock:
            def write(connection: sqlite3.Connection) -> StoredResponse:
                self._validate_token(connection, token)
                response = self._require_open_response(connection, response_id)
                if attempt.response_id != response_id:
                    raise CoreStateError("ATTEMPT_RESPONSE_MISMATCH")
                if any(item.execution_attempt_id == attempt.execution_attempt_id for item in response.attempts):
                    return response
                response = replace(response, attempts=response.attempts + (attempt,), revision=response.revision + 1)
                self._append_fact(connection, self._projection_fact("execution_attempt_added", response_id.value, response.revision, {"kind": "response", "response": self._encode_response(response)}))
                self._write_response(connection, response)
                return response

            return self._write(write)

    async def commit_segment(self, token: CoreAuthorityToken, response_id: ResponseId, expected_response_revision: int, segment: AgentOutputSegmentCommitted) -> StoredResponse:
        async with self._lock:
            return self._write(lambda connection: self._commit_segment_tx(connection, token, response_id, expected_response_revision, segment))

    async def commit_segment_commit(self, token: CoreAuthorityToken, response_id: ResponseId, expected_response_revision: int, segment: AgentOutputSegmentCommitted) -> StateCommit[StoredResponse]:
        identity = "segment:" + response_id.value + ":" + segment.segment_id.value
        async with self._lock:
            return self._write_commit(identity, lambda connection: self._commit_segment_tx(connection, token, response_id, expected_response_revision, segment), lambda: self._reconcile_segment(response_id, segment))

    async def finalize_response(self, token: CoreAuthorityToken, response_id: ResponseId, expected_response_revision: int) -> StoredResponse:
        async with self._lock:
            return self._terminal_response(token, response_id, expected_response_revision, None)

    async def abort_response(self, token: CoreAuthorityToken, response_id: ResponseId, expected_response_revision: int, reason: ResponseAbortReason) -> StoredResponse:
        async with self._lock:
            return self._terminal_response(token, response_id, expected_response_revision, reason)

    async def terminalize_turn(self, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, expected_turn_revision: int, state: TurnState, abort_reason: Optional[AbortReason] = None) -> StoredTurn:
        async with self._lock:
            return self._write(lambda connection: self._terminalize_turn_tx(connection, token, conversation_ref, turn_id, expected_turn_revision, state, abort_reason))

    async def terminalize_turn_commit(self, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, expected_turn_revision: int, state: TurnState, abort_reason: Optional[AbortReason] = None) -> StateCommit[StoredTurn]:
        identity = "terminal:" + _turn_key(conversation_ref, turn_id) + ":" + state.value + ":" + ("" if abort_reason is None else abort_reason.value)
        async with self._lock:
            return self._write_commit(identity, lambda connection: self._terminalize_turn_tx(connection, token, conversation_ref, turn_id, expected_turn_revision, state, abort_reason), lambda: self._reconcile_terminal(conversation_ref, turn_id, state, abort_reason))

    async def commit_operation_intent(self, token: CoreAuthorityToken, intent: OperationIntentCommitted) -> StateCommit[StoredOperation]:
        identity = "operation-intent:" + intent.operation.operation_id.value
        async with self._lock:
            return self._write_commit(identity, lambda connection: self._commit_operation_intent_tx(connection, token, intent), lambda: self._operation_from_connection(self._connection, intent.operation.operation_id))

    async def commit_memory_observation_intent(
        self,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
    ) -> StateCommit[MemoryCausalOperation]:
        """One SQLite transaction binds a durable R2 intent to exactly one lane sequence."""

        if observation.operation_id != intent.operation.operation_id:
            raise CoreStateError("MEMORY_OPERATION_INTENT_MISMATCH")
        identity = "memory-observation-intent:" + observation.operation_id.value
        async with self._lock:
            return self._write_commit(
                identity,
                lambda connection: self._commit_memory_observation_intent_tx(connection, token, observation, intent),
                lambda: self._memory_causal_operation_from_connection(self._connection, observation),
            )

    async def get_memory_causal_lane(self, observation: MemoryObservationOperation) -> MemoryCausalLaneSnapshot:
        async with self._lock:
            return self._memory_causal_lane_from_connection(self._connection, observation)

    async def project_memory_operation_outcome(
        self, token: CoreAuthorityToken, observation: MemoryObservationOperation,
    ) -> StateCommit[MemoryCausalLaneSnapshot]:
        """Persist the R2-D outcome projection; restarts never infer a safer history."""

        identity = "memory-causal-outcome:" + observation.operation_id.value
        async with self._lock:
            return self._write_commit(
                identity,
                lambda connection: self._project_memory_operation_outcome_tx(connection, token, observation),
                lambda: self._memory_causal_lane_from_connection(self._connection, observation),
            )

    async def reconcile_memory_observation(
        self,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        resolution: MemoryCausalResolution,
        proof: MemoryReconciliationProof,
    ) -> StateCommit[MemoryCausalLaneSnapshot]:
        identity = "memory-causal-reconcile:" + observation.operation_id.value + ":" + resolution.value
        async with self._lock:
            return self._write_commit(
                identity,
                lambda connection: self._reconcile_memory_observation_tx(connection, token, observation, resolution, proof),
                lambda: self._memory_causal_lane_from_connection(self._connection, observation),
            )

    async def get_operation(self, operation_id: OperationId) -> StoredOperation:
        async with self._lock:
            return self._operation_from_connection(self._connection, operation_id)

    async def commit_memory_dispatch_attempt(
        self,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
    ) -> StateCommit[MemoryDispatchAttempt]:
        identity = "memory-dispatch-attempt:" + observation.operation_id.value + ":" + intent.provider_binding.binding_id.value
        async with self._lock:
            return self._write_commit(
                identity,
                lambda connection: self._commit_memory_dispatch_attempt_tx(connection, token, observation, intent),
                lambda: self._latest_memory_dispatch_attempt_from_connection(self._connection, observation.operation_id),
            )

    async def get_memory_dispatch_attempts(
        self, operation_id: OperationId,
    ) -> Tuple[MemoryDispatchAttempt, ...]:
        async with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM memory_dispatch_attempts WHERE operation_id = ? ORDER BY attempt_number",
                (operation_id.value,),
            ).fetchall()
            return tuple(MemoryDispatchAttempt.model_validate_json(str(row["payload"])) for row in rows)

    async def mark_dispatch_attempted(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]:
        return await self._mark_operation(token, operation_id, expected_revision, DurableOperationState.DISPATCH_ATTEMPTED, None)

    async def mark_succeeded(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]:
        return await self._mark_operation(token, operation_id, expected_revision, DurableOperationState.SUCCEEDED, None)

    async def mark_failed(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]:
        return await self._mark_operation(token, operation_id, expected_revision, DurableOperationState.FAILED, None)

    async def mark_outcome_unknown(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int) -> StateCommit[StoredOperation]:
        return await self._mark_operation(token, operation_id, expected_revision, DurableOperationState.OUTCOME_UNKNOWN, None)

    async def mark_not_dispatched(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int, reason: NotDispatchedReason) -> StateCommit[StoredOperation]:
        return await self._mark_operation(token, operation_id, expected_revision, DurableOperationState.NOT_DISPATCHED, reason)

    async def reconcile_operation(self, operation_id: OperationId) -> StoredOperation:
        return await self.get_operation(operation_id)

    async def _mark_operation(self, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int, state: DurableOperationState, reason: Optional[NotDispatchedReason]) -> StateCommit[StoredOperation]:
        identity = "operation:" + operation_id.value + ":" + state.value + ":" + ("" if reason is None else reason.value)
        async with self._lock:
            return self._write_commit(identity, lambda connection: self._mark_operation_tx(connection, token, operation_id, expected_revision, state, reason), lambda: self._operation_from_connection(self._connection, operation_id))

    async def get_conversation_context(self, conversation_ref: ConversationRef, excluding_turn_id: TurnId) -> Tuple[ContextEntry, ...]:
        async with self._lock:
            self._require_serving()
            rows = self._connection.execute("SELECT turn_key, payload FROM turns WHERE conversation_key = ? ORDER BY rowid", (_conversation_key(conversation_ref),)).fetchall()
            entries: List[ContextEntry] = []
            for row in rows:
                turn = self._decode_turn(_load_json(str(row["payload"])))
                if turn.turn_ref.turn_id == excluding_turn_id:
                    continue
                entries.extend(ContextEntry(role=ContextRole.USER, text=part.text) for part in turn.turn_input.parts if hasattr(part, "text"))
                if turn.response_id is not None:
                    response_row = self._connection.execute("SELECT payload FROM responses WHERE response_id = ?", (turn.response_id.value,)).fetchone()
                    if response_row is not None:
                        response = self._decode_response(_load_json(str(response_row["payload"])))
                        entries.extend(ContextEntry(role=ContextRole.ASSISTANT, text=segment.text) for segment in response.segments)
            return tuple(entries)

    async def watch_response_events(self, response_id: ResponseId, after_event_sequence: int) -> AsyncIterator[ResponseEvent]:
        cursor = after_event_sequence
        while True:
            response = await self.get_response(response_id)
            replay = tuple(event for event in response.events if event.event_sequence > cursor)
            for event in replay:
                cursor = event.event_sequence
                yield event
            if response.state in _TERMINAL_RESPONSES:
                return
            await asyncio.sleep(0.01)

    async def active_claim(self, conversation_ref: ConversationRef) -> Optional[TurnId]:
        async with self._lock:
            self._require_serving()
            row = self._connection.execute("SELECT turn_key FROM claims WHERE conversation_key = ?", (_conversation_key(conversation_ref),)).fetchone()
            if row is None:
                return None
            turn = self._turn_from_connection(self._connection, str(row["turn_key"]))
            return turn.turn_ref.turn_id

    async def append_fact(self, token: CoreAuthorityToken, fact: FactEnvelope) -> FactCommitResult:
        """Append a fact with stable FactId idempotency and explicit certainty."""

        async with self._lock:
            def write(connection: sqlite3.Connection) -> FactCommitResult:
                self._validate_token(connection, token)
                return self._append_fact(connection, fact)

            result = self._write(write)
            if self._unknown_commit_once:
                self._unknown_commit_once = False
                return FactCommitResult(fact_id=fact.fact_id, certainty=CommitCertainty.OUTCOME_UNKNOWN, aggregate_revision=fact.aggregate_revision)
            return result

    async def get_fact(self, fact_id: FactId) -> Optional[FactEnvelope]:
        async with self._lock:
            self._require_serving()
            row = self._connection.execute("SELECT fact_id, fact_type, fact_schema_version, aggregate_id, aggregate_revision, recorded_at, payload FROM facts WHERE fact_id = ?", (fact_id.value,)).fetchone()
            return None if row is None else self._decode_fact(row)

    async def reconcile_fact(self, fact_id: FactId) -> Optional[FactEnvelope]:
        """Stable FactId reconciliation after an OUTCOME_UNKNOWN commit result."""

        return await self.get_fact(fact_id)

    async def rebuild_projections(self, token: CoreAuthorityToken) -> None:
        """Recreate projections solely from the append-only fact ledger."""

        async with self._lock:
            def write(connection: sqlite3.Connection) -> None:
                self._validate_token(connection, token)
                facts = connection.execute("SELECT fact_id, fact_type, fact_schema_version, aggregate_id, aggregate_revision, recorded_at, payload FROM facts ORDER BY rowid").fetchall()
                connection.execute("DELETE FROM claims")
                connection.execute("DELETE FROM responses")
                connection.execute("DELETE FROM turns")
                connection.execute("DELETE FROM conversations")
                connection.execute("DELETE FROM operations")
                connection.execute("DELETE FROM memory_causal_lanes")
                connection.execute("DELETE FROM memory_observation_operations")
                connection.execute("DELETE FROM memory_dispatch_attempts")
                for row in facts:
                    fact = self._decode_fact(row)
                    self._apply_projection_fact(connection, fact)
                self._rebuild_claims(connection)

            try:
                self._write(write)
            except CoreStateError as error:
                if error.code in {"INCOMPATIBLE_FACT_SCHEMA", "INCOMPATIBLE_SCHEMA"}:
                    self._serving = False
                raise

    def _initialize(self, requested_id: Optional[StoreAuthorityId]) -> None:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS facts (fact_id TEXT PRIMARY KEY, fact_type TEXT NOT NULL, fact_schema_version INTEGER NOT NULL, aggregate_id TEXT NOT NULL, aggregate_revision INTEGER NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(aggregate_id, aggregate_revision))")
            connection.execute("CREATE TABLE IF NOT EXISTS conversations (conversation_key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS turns (turn_key TEXT PRIMARY KEY, conversation_key TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS responses (response_id TEXT PRIMARY KEY, turn_key TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS claims (conversation_key TEXT PRIMARY KEY, turn_key TEXT UNIQUE NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS operations (operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS memory_causal_lanes (scope_key TEXT PRIMARY KEY, logical_provider_domain TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS memory_observation_operations (operation_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, logical_provider_domain TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS memory_dispatch_attempts (operation_id TEXT NOT NULL, attempt_number INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(operation_id, attempt_number))")
            row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
            if row is None:
                authority = requested_id or StoreAuthorityId(value="sqlite-store-" + uuid.uuid4().hex)
                self._set_meta(connection, "schema_version", _SCHEMA_VERSION)
                self._set_meta(connection, "store_authority_id", authority.value)
                self._set_meta(connection, "authority_epoch", "0")
                self._set_meta(connection, "active_authority", "")
            elif str(row["value"]) != _SCHEMA_VERSION:
                raise CoreStateError("INCOMPATIBLE_SCHEMA")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def _acquire_authority(self, core_instance_id: CoreInstanceId) -> CoreAuthorityToken:
        def write(connection: sqlite3.Connection) -> CoreAuthorityToken:
            if self._meta(connection, "active_authority") and self._writer_lock is None:
                raise CoreStateError("CORE_AUTHORITY_ALREADY_OWNED")
            epoch = int(self._meta(connection, "authority_epoch")) + 1
            token = CoreAuthorityToken(core_instance_id=core_instance_id, store_authority_id=StoreAuthorityId(value=self._meta(connection, "store_authority_id")), authority_epoch=epoch, token_nonce=uuid.uuid4().hex)
            self._set_meta(connection, "authority_epoch", str(epoch))
            self._set_meta(connection, "active_authority", _json(_contract(token)))
            return token

        return self._write(write)

    def _acquire_writer_lock(self) -> None:
        if self._path == ":memory:" or self._writer_lock is not None:
            if self._path != ":memory:" and self._writer_lock is not None:
                raise CoreStateError("CORE_AUTHORITY_ALREADY_OWNED")
            return
        lock_path = self._path + ".writer.lock"
        if lock_path in _LOCAL_WRITER_LOCKS:
            raise CoreStateError("CORE_AUTHORITY_ALREADY_OWNED")
        handle = open(lock_path, "a+b")
        try:
            handle.seek(0)
            if os.path.getsize(lock_path) == 0:
                handle.write(b"0")
                handle.flush()
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise CoreStateError("CORE_AUTHORITY_ALREADY_OWNED") from error
        self._writer_lock = handle
        _LOCAL_WRITER_LOCKS.add(lock_path)

    def _release_writer_lock(self) -> None:
        if self._writer_lock is None:
            return
        try:
            # Closing a locked Windows handle releases the byte-range lock.
            pass
        finally:
            self._writer_lock.close()
            _LOCAL_WRITER_LOCKS.discard(self._path + ".writer.lock")
            self._writer_lock = None

    def _terminal_response(self, token: CoreAuthorityToken, response_id: ResponseId, expected_revision: int, abort_reason: Optional[ResponseAbortReason]) -> StoredResponse:
        def write(connection: sqlite3.Connection) -> StoredResponse:
            self._validate_token(connection, token)
            response = self._require_open_response(connection, response_id)
            if response.revision != expected_revision:
                raise CoreStateError("RESPONSE_REVISION_CONFLICT")
            if abort_reason is None:
                event: ResponseEvent = ResponseFinalizedEvent(event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response_id, event_sequence=len(response.events) + 1)
                response = replace(response, state=ResponseState.FINALIZED, events=response.events + (event,), revision=response.revision + 1)
                fact_type = "response_finalized"
            else:
                event = ResponseAbortedEvent(event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response_id, event_sequence=len(response.events) + 1, reason=abort_reason)
                response = replace(response, state=ResponseState.ABORTED, abort_reason=abort_reason, events=response.events + (event,), revision=response.revision + 1)
                fact_type = "response_aborted"
            self._append_fact(connection, self._projection_fact(fact_type, response_id.value, response.revision, {"kind": "response", "response": self._encode_response(response)}))
            self._write_response(connection, response)
            return response

        return self._write(write)

    def _projection_fact(self, fact_type: str, aggregate_id: str, revision: int, payload: Dict[str, Any]) -> FactEnvelope:
        return FactEnvelope(fact_id=_fact_id(fact_type + ":" + aggregate_id + ":" + str(revision)), fact_type=fact_type, fact_schema_version=1, aggregate_id=aggregate_id, aggregate_revision=revision, recorded_at=utc_now(), payload=payload)

    def _append_fact(self, connection: sqlite3.Connection, fact: FactEnvelope) -> FactCommitResult:
        row = connection.execute("SELECT fact_type, fact_schema_version, aggregate_id, aggregate_revision, recorded_at, payload FROM facts WHERE fact_id = ?", (fact.fact_id.value,)).fetchone()
        if row is not None:
            current = self._decode_fact(row, fact.fact_id)
            if current != fact:
                raise CoreStateError("FACT_ID_CONFLICT")
            return FactCommitResult(fact_id=fact.fact_id, certainty=CommitCertainty.COMMITTED, aggregate_revision=fact.aggregate_revision)
        latest = connection.execute("SELECT MAX(aggregate_revision) AS revision FROM facts WHERE aggregate_id = ?", (fact.aggregate_id,)).fetchone()
        current_revision = 0 if latest is None or latest["revision"] is None else int(latest["revision"])
        if fact.aggregate_revision != current_revision + 1:
            raise CoreStateError("FACT_REVISION_CONFLICT")
        connection.execute("INSERT INTO facts(fact_id, fact_type, fact_schema_version, aggregate_id, aggregate_revision, recorded_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)", (fact.fact_id.value, fact.fact_type, fact.fact_schema_version, fact.aggregate_id, fact.aggregate_revision, fact.recorded_at.value.isoformat(), _json(fact.payload)))
        return FactCommitResult(fact_id=fact.fact_id, certainty=CommitCertainty.COMMITTED, aggregate_revision=fact.aggregate_revision)

    def _apply_projection_fact(self, connection: sqlite3.Connection, fact: FactEnvelope) -> None:
        kind = fact.payload.get("kind")
        if kind == "conversation":
            conversation = ConversationRef.model_validate(fact.payload["conversation"])
            connection.execute("INSERT OR REPLACE INTO conversations(conversation_key, payload) VALUES (?, ?)", (_conversation_key(conversation), _json(_contract(conversation))))
        elif kind == "turn":
            turn = self._decode_turn(fact.payload["turn"])
            connection.execute("INSERT OR REPLACE INTO turns(turn_key, conversation_key, payload) VALUES (?, ?, ?)", (self._turn_key_from_turn(turn), _conversation_key(turn.conversation_ref), _json(self._encode_turn(turn))))
        elif kind == "response":
            response = self._decode_response(fact.payload["response"])
            connection.execute("INSERT OR REPLACE INTO responses(response_id, turn_key, payload) VALUES (?, ?, ?)", (response.response_id.value, response.turn_key, _json(self._encode_response(response))))
        elif kind == "operation":
            operation = self._decode_operation(fact.payload["operation"])
            connection.execute("INSERT OR REPLACE INTO operations(operation_id, fingerprint, payload) VALUES (?, ?, ?)", (operation.intent.operation.operation_id.value, operation.intent.operation.request_fingerprint.value, _json(self._encode_operation(operation))))
        elif kind == "memory_causal_lane":
            snapshot = MemoryCausalLaneSnapshot.model_validate_json(_json(fact.payload["snapshot"]))
            semantic = None
            if "semantic_operation" in fact.payload:
                semantic = MemoryObservationOperation.model_validate_json(_json(fact.payload["semantic_operation"]))
            self._write_memory_lane_projection(connection, snapshot, semantic)
        elif kind == "memory_dispatch_attempt":
            attempt = MemoryDispatchAttempt.model_validate_json(_json(fact.payload["attempt"]))
            self._write_memory_dispatch_attempt_projection(connection, attempt)

    def _rebuild_claims(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT turn_key, conversation_key, payload FROM turns").fetchall()
        for row in rows:
            turn = self._decode_turn(_load_json(str(row["payload"])))
            if turn.state not in _TERMINAL_TURNS:
                connection.execute("INSERT INTO claims(conversation_key, turn_key) VALUES (?, ?)", (str(row["conversation_key"]), str(row["turn_key"])))

    def _commit_operation_intent_tx(self, connection: sqlite3.Connection, token: CoreAuthorityToken, intent: OperationIntentCommitted) -> StoredOperation:
        self._validate_token(connection, token)
        operation_id = intent.operation.operation_id
        row = connection.execute("SELECT fingerprint, payload FROM operations WHERE operation_id = ?", (operation_id.value,)).fetchone()
        if row is not None:
            if str(row["fingerprint"]) != intent.operation.request_fingerprint.value:
                raise CoreStateError("OPERATION_ID_CONFLICT")
            return self._decode_operation(_load_json(str(row["payload"])))
        operation = StoredOperation(intent=intent, state=DurableOperationState.INTENT_COMMITTED, revision=1)
        self._append_fact(connection, self._projection_fact("operation_intent_committed", "operation:" + operation_id.value, operation.revision, {"kind": "operation", "operation": self._encode_operation(operation)}))
        connection.execute("INSERT INTO operations(operation_id, fingerprint, payload) VALUES (?, ?, ?)", (operation_id.value, intent.operation.request_fingerprint.value, _json(self._encode_operation(operation))))
        return operation

    def _commit_memory_observation_intent_tx(
        self,
        connection: sqlite3.Connection,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
    ) -> MemoryCausalOperation:
        try:
            observation.validate_durable_intent(intent)
            new_semantic_operation = self._validate_or_record_memory_semantic_operation(connection, observation)
        except ValueError as error:
            raise CoreStateError(str(error)) from error
        stored = self._commit_operation_intent_tx(connection, token, intent)
        try:
            observation.validate_durable_intent(stored.intent)
        except ValueError as error:
            # R2 intentionally identifies an existing operation by ID and
            # fingerprint only.  The Memory bridge has a narrower semantic
            # contract, so reject a same-fingerprint non-Memory reuse before
            # allocating a causal sequence.
            raise CoreStateError("MEMORY_OPERATION_INTENT_CONFLICT") from error
        try:
            lane = MemoryCausalLane(self._memory_causal_lane_from_connection(connection, observation))
        except CoreStateError as error:
            if error.code != "MEMORY_CAUSAL_LANE_NOT_FOUND":
                raise
            lane = MemoryCausalLane.empty(observation)
        try:
            causal_operation = lane.allocate(observation)
        except MemoryCausalLaneError as error:
            raise CoreStateError(str(error)) from error
        snapshot = lane.snapshot()
        self._write_memory_lane_projection(connection, snapshot, observation if new_semantic_operation else None)
        if new_semantic_operation:
            self._append_memory_causal_fact(connection, "memory_causal_operation_allocated", snapshot, observation)
        return causal_operation

    def _commit_memory_dispatch_attempt_tx(
        self,
        connection: sqlite3.Connection,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        intent: OperationIntentCommitted,
    ) -> MemoryDispatchAttempt:
        self._validate_token(connection, token)
        try:
            observation.validate_durable_intent(intent)
            observation.validate_durable_intent(self._operation_from_connection(connection, observation.operation_id).intent)
        except ValueError as error:
            raise CoreStateError("MEMORY_OPERATION_INTENT_CONFLICT") from error
        lane = MemoryCausalLane(self._memory_causal_lane_from_connection(connection, observation))
        if not lane.can_dispatch(observation.operation_id):
            raise CoreStateError("MEMORY_CAUSAL_DISPATCH_BLOCKED")
        row = connection.execute(
            "SELECT MAX(attempt_number) AS attempt_number FROM memory_dispatch_attempts WHERE operation_id = ?",
            (observation.operation_id.value,),
        ).fetchone()
        attempt_number = 1 if row is None or row["attempt_number"] is None else int(row["attempt_number"]) + 1
        dispatch_attempt = DispatchAttempt(
            operation_attempt_id=OperationAttemptId(value="memory-attempt-" + uuid.uuid4().hex),
            operation_id=observation.operation_id,
            attempt_number=attempt_number,
            provider_binding_id=intent.provider_binding.binding_id,
            dispatch_started_at=utc_now(),
        )
        attempt = MemoryDispatchAttempt(
            dispatch_attempt=dispatch_attempt,
            logical_provider_domain=observation.logical_provider_domain,
            provider_binding=intent.provider_binding,
        )
        self._append_fact(
            connection,
            self._projection_fact(
                "memory_dispatch_attempt_committed",
                "memory-dispatch:" + observation.operation_id.value,
                attempt_number,
                {"kind": "memory_dispatch_attempt", "attempt": attempt.model_dump(mode="json")},
            ),
        )
        self._write_memory_dispatch_attempt_projection(connection, attempt)
        return attempt

    def _memory_causal_lane_from_connection(
        self, connection: sqlite3.Connection, observation: MemoryObservationOperation,
    ) -> MemoryCausalLaneSnapshot:
        row = connection.execute(
            "SELECT logical_provider_domain, payload FROM memory_causal_lanes WHERE scope_key = ?",
            (observation.scope_ref.scope_id.value,),
        ).fetchone()
        if row is None:
            raise CoreStateError("MEMORY_CAUSAL_LANE_NOT_FOUND")
        if str(row["logical_provider_domain"]) != observation.logical_provider_domain:
            raise CoreStateError("CROSS_DOMAIN_UNCERTAINTY_REJECTED")
        snapshot = MemoryCausalLaneSnapshot.model_validate_json(str(row["payload"]))
        if snapshot.state.scope_ref != observation.scope_ref:
            raise CoreStateError("MEMORY_CAUSAL_SCOPE_MISMATCH")
        return snapshot

    def _latest_memory_dispatch_attempt_from_connection(
        self, connection: sqlite3.Connection, operation_id: OperationId,
    ) -> MemoryDispatchAttempt:
        row = self._read_one(
            "SELECT payload FROM memory_dispatch_attempts WHERE operation_id = ? ORDER BY attempt_number DESC LIMIT 1",
            (operation_id.value,),
            "MEMORY_DISPATCH_ATTEMPT_NOT_FOUND",
        )
        return MemoryDispatchAttempt.model_validate_json(str(row["payload"]))

    def _memory_causal_operation_from_connection(
        self, connection: sqlite3.Connection, observation: MemoryObservationOperation,
    ) -> MemoryCausalOperation:
        lane = self._memory_causal_lane_from_connection(connection, observation)
        for causal_operation in lane.operations:
            if causal_operation.operation_id == observation.operation_id:
                return causal_operation
        raise CoreStateError("MEMORY_CAUSAL_OPERATION_NOT_FOUND")

    def _project_memory_operation_outcome_tx(
        self,
        connection: sqlite3.Connection,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
    ) -> MemoryCausalLaneSnapshot:
        self._validate_token(connection, token)
        lane = MemoryCausalLane(self._memory_causal_lane_from_connection(connection, observation))
        before = lane.snapshot()
        try:
            lane.record_durable_operation(self._operation_from_connection(connection, observation.operation_id))
        except MemoryCausalLaneError as error:
            raise CoreStateError(str(error)) from error
        snapshot = lane.snapshot()
        if snapshot != before:
            self._write_memory_lane_projection(connection, snapshot)
            self._append_memory_causal_fact(connection, "memory_causal_operation_resolved", snapshot)
        return snapshot

    def _reconcile_memory_observation_tx(
        self,
        connection: sqlite3.Connection,
        token: CoreAuthorityToken,
        observation: MemoryObservationOperation,
        resolution: MemoryCausalResolution,
        proof: MemoryReconciliationProof,
    ) -> MemoryCausalLaneSnapshot:
        self._validate_token(connection, token)
        lane = MemoryCausalLane(self._memory_causal_lane_from_connection(connection, observation))
        try:
            lane.reconcile_same_domain(
                observation,
                self._operation_from_connection(connection, observation.operation_id),
                resolution,
                proof,
            )
        except MemoryCausalLaneError as error:
            raise CoreStateError(str(error)) from error
        snapshot = lane.snapshot()
        self._write_memory_lane_projection(connection, snapshot)
        self._append_memory_causal_fact(
            connection,
            "memory_causal_operation_reconciled",
            snapshot,
            reconciliation_proof=proof,
        )
        return snapshot

    def _validate_or_record_memory_semantic_operation(
        self, connection: sqlite3.Connection, observation: MemoryObservationOperation,
    ) -> bool:
        row = connection.execute(
            "SELECT payload FROM memory_observation_operations WHERE operation_id = ?", (observation.operation_id.value,)
        ).fetchone()
        encoded = observation.model_dump_json()
        if row is not None:
            if str(row["payload"]) != encoded:
                raise CoreStateError("MEMORY_OPERATION_SEMANTIC_CONFLICT")
            return False
        connection.execute(
            "INSERT INTO memory_observation_operations(operation_id, scope_key, logical_provider_domain, payload) VALUES (?, ?, ?, ?)",
            (observation.operation_id.value, observation.scope_ref.scope_id.value, observation.logical_provider_domain, encoded),
        )
        return True

    def _write_memory_lane_projection(
        self,
        connection: sqlite3.Connection,
        snapshot: MemoryCausalLaneSnapshot,
        semantic_operation: Optional[MemoryObservationOperation] = None,
    ) -> None:
        state = snapshot.state
        connection.execute(
            "INSERT INTO memory_causal_lanes(scope_key, logical_provider_domain, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(scope_key) DO UPDATE SET logical_provider_domain = excluded.logical_provider_domain, payload = excluded.payload",
            (state.scope_ref.scope_id.value, state.logical_provider_domain, snapshot.model_dump_json()),
        )
        if semantic_operation is not None:
            self._validate_or_record_memory_semantic_operation(connection, semantic_operation)

    @staticmethod
    def _write_memory_dispatch_attempt_projection(
        connection: sqlite3.Connection, attempt: MemoryDispatchAttempt,
    ) -> None:
        connection.execute(
            "INSERT INTO memory_dispatch_attempts(operation_id, attempt_number, payload) VALUES (?, ?, ?)",
            (
                attempt.dispatch_attempt.operation_id.value,
                attempt.dispatch_attempt.attempt_number,
                attempt.model_dump_json(),
            ),
        )

    def _append_memory_causal_fact(
        self,
        connection: sqlite3.Connection,
        fact_type: str,
        snapshot: MemoryCausalLaneSnapshot,
        semantic_operation: Optional[MemoryObservationOperation] = None,
        reconciliation_proof: Optional[MemoryReconciliationProof] = None,
    ) -> None:
        payload = {"kind": "memory_causal_lane", "snapshot": snapshot.model_dump(mode="json")}
        if semantic_operation is not None:
            payload["semantic_operation"] = semantic_operation.model_dump(mode="json")
        if reconciliation_proof is not None:
            payload["reconciliation_proof"] = reconciliation_proof.model_dump(mode="json")
        aggregate_id = "memory-causal:" + snapshot.state.scope_ref.scope_id.value
        self._append_fact(
            connection,
            self._projection_fact(fact_type, aggregate_id, snapshot.state.revision - 1, payload),
        )

    def _mark_operation_tx(self, connection: sqlite3.Connection, token: CoreAuthorityToken, operation_id: OperationId, expected_revision: int, state: DurableOperationState, reason: Optional[NotDispatchedReason]) -> StoredOperation:
        self._validate_token(connection, token)
        operation = self._operation_from_connection(connection, operation_id)
        if operation.state in {DurableOperationState.SUCCEEDED, DurableOperationState.FAILED, DurableOperationState.NOT_DISPATCHED}:
            return operation
        if operation.state == DurableOperationState.OUTCOME_UNKNOWN and state != DurableOperationState.DISPATCH_ATTEMPTED:
            return operation
        if operation.revision != expected_revision:
            raise CoreStateError("OPERATION_REVISION_CONFLICT")
        operation = StoredOperation(intent=operation.intent, state=state, revision=operation.revision + 1, attempts=operation.attempts, outcome=None, not_dispatched_reason=reason)
        self._append_fact(connection, self._projection_fact("operation_" + state.value, "operation:" + operation_id.value, operation.revision, {"kind": "operation", "operation": self._encode_operation(operation)}))
        connection.execute("UPDATE operations SET payload = ? WHERE operation_id = ?", (_json(self._encode_operation(operation)), operation_id.value))
        return operation

    def _try_admit_turn_tx(self, connection: sqlite3.Connection, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, fingerprint: TurnFingerprint, snapshot: TurnSnapshot, turn_input: TurnInput) -> TurnAdmissionResult:
        self._validate_token(connection, token); conversation_key = _conversation_key(conversation_ref)
        row = connection.execute("SELECT payload FROM conversations WHERE conversation_key = ?", (conversation_key,)).fetchone()
        if row is None or ConversationRef.model_validate(_load_json(str(row["payload"]))) != conversation_ref: return TurnAdmissionResult(status=TurnAdmissionStatus.REJECTED, reason="CONVERSATION_NOT_FOUND")
        key = _turn_key(conversation_ref, turn_id); fingerprint_value = fingerprint.model_dump_json(); existing = connection.execute("SELECT payload FROM turns WHERE turn_key = ?", (key,)).fetchone()
        if existing is not None:
            turn = self._decode_turn(_load_json(str(existing["payload"])))
            return TurnAdmissionResult(status=TurnAdmissionStatus.TURN_ID_CONFLICT, turn_id=turn_id) if turn.fingerprint != fingerprint_value else self._existing_admission(connection, turn)
        if connection.execute("SELECT 1 FROM claims WHERE conversation_key = ?", (conversation_key,)).fetchone() is not None: return TurnAdmissionResult(status=TurnAdmissionStatus.CONVERSATION_BUSY, reason="CONVERSATION_BUSY")
        turn = StoredTurn(conversation_ref=conversation_ref, turn_ref=TurnRef(conversation_id=conversation_ref.conversation_id, turn_id=turn_id), fingerprint=fingerprint_value, snapshot=snapshot, turn_input=turn_input)
        self._append_fact(connection, self._projection_fact("turn_input_committed", key, turn.revision, {"kind":"turn", "turn":self._encode_turn(turn)})); connection.execute("INSERT INTO turns(turn_key, conversation_key, payload) VALUES (?, ?, ?)", (key, conversation_key, _json(self._encode_turn(turn)))); connection.execute("INSERT INTO claims(conversation_key, turn_key) VALUES (?, ?)", (conversation_key, key))
        return TurnAdmissionResult(status=TurnAdmissionStatus.NEWLY_ADMITTED, turn_id=turn_id)

    def _commit_segment_tx(self, connection: sqlite3.Connection, token: CoreAuthorityToken, response_id: ResponseId, expected: int, segment: AgentOutputSegmentCommitted) -> StoredResponse:
        self._validate_token(connection, token); response = self._require_open_response(connection, response_id)
        if response.revision != expected:
            for present in response.segments:
                if present.segment_id == segment.segment_id:
                    if present == segment: return response
                    raise CoreStateError("SEGMENT_ID_CONFLICT")
            raise CoreStateError("RESPONSE_REVISION_CONFLICT")
        if segment.response_id != response_id: raise CoreStateError("SEGMENT_RESPONSE_MISMATCH")
        if response.segments and segment.sequence <= response.segments[-1].sequence: raise CoreStateError("SEGMENT_SEQUENCE_CONFLICT")
        event = SegmentCommittedEvent(event_id=ResponseEventId(value="event-" + uuid.uuid4().hex), response_id=response_id, event_sequence=len(response.events)+1, segment_id=segment.segment_id, segment_sequence=segment.sequence, text=segment.text, language=segment.language)
        response = replace(response, segments=response.segments+(segment,), events=response.events+(event,), revision=response.revision+1); self._append_fact(connection, self._projection_fact("response_segment_committed", response_id.value, response.revision, {"kind":"response", "response":self._encode_response(response)})); self._write_response(connection, response)
        return response

    def _terminalize_turn_tx(self, connection: sqlite3.Connection, token: CoreAuthorityToken, conversation_ref: ConversationRef, turn_id: TurnId, expected: int, state: TurnState, abort_reason: Optional[AbortReason]) -> StoredTurn:
        self._validate_token(connection, token); key = _turn_key(conversation_ref, turn_id); turn = self._turn_from_connection(connection, key)
        if turn.state in _TERMINAL_TURNS: return turn
        if turn.revision != expected: raise CoreStateError("TURN_REVISION_CONFLICT")
        if connection.execute("SELECT 1 FROM claims WHERE conversation_key = ? AND turn_key = ?", (_conversation_key(conversation_ref), key)).fetchone() is None: raise CoreStateError("CONVERSATION_CLAIM_MISMATCH")
        if not validate_turn_transition(turn.state, state): raise CoreStateError("INVALID_TURN_TRANSITION")
        if state == TurnState.ABORTED and abort_reason is None: raise CoreStateError("ABORT_REASON_REQUIRED")
        if state != TurnState.ABORTED and abort_reason is not None: raise CoreStateError("UNEXPECTED_ABORT_REASON")
        turn = replace(turn, state=state, abort_reason=abort_reason, revision=turn.revision+1); self._append_fact(connection, self._projection_fact("turn_terminalized", key, turn.revision, {"kind":"turn", "turn":self._encode_turn(turn)})); self._write_turn(connection, key, turn); connection.execute("DELETE FROM claims WHERE conversation_key = ? AND turn_key = ?", (_conversation_key(conversation_ref), key)); return turn

    def _reconcile_admission(self, ref: ConversationRef, turn_id: TurnId, fingerprint: TurnFingerprint) -> TurnAdmissionResult:
        turn = self._turn_from_connection(self._connection, _turn_key(ref, turn_id))
        if turn.fingerprint != fingerprint.model_dump_json(): raise CoreStateError("TURN_ID_CONFLICT")
        return self._existing_admission(self._connection, turn)

    def _reconcile_segment(self, response_id: ResponseId, segment: AgentOutputSegmentCommitted) -> StoredResponse:
        response = self._response_from_connection(self._connection, response_id)
        for present in response.segments:
            if present.segment_id == segment.segment_id:
                if present == segment: return response
                raise CoreStateError("SEGMENT_ID_CONFLICT")
        raise CoreStateError("RECONCILIATION_NOT_FOUND")

    def _reconcile_terminal(self, ref: ConversationRef, turn_id: TurnId, state: TurnState, reason: Optional[AbortReason]) -> StoredTurn:
        turn = self._turn_from_connection(self._connection, _turn_key(ref, turn_id))
        if turn.state == state and turn.abort_reason == reason: return turn
        raise CoreStateError("RECONCILIATION_NOT_FOUND")

    def _write(self, action: Callable[[sqlite3.Connection], _WriteResult]) -> _WriteResult:
        self._require_serving()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = action(connection)
            connection.execute("COMMIT")
            return result
        except CoreStateError:
            connection.execute("ROLLBACK")
            raise
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as error:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise CoreStateError("STORE_CONTENTION") from error
            self._serving = False
            raise CoreStateError("STORE_NOT_SERVING") from error

    def _write_commit(self, identity: str, action: Callable[[sqlite3.Connection], _WriteResult], reconcile: Callable[[], _WriteResult]) -> StateCommit[_WriteResult]:
        """Own the semantic mutation's BEGIN/COMMIT boundary and certainty."""
        self._require_serving()
        connection = self._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            value = action(connection)
            if self._semantic_not_committed_once:
                self._semantic_not_committed_once = False
                connection.execute("ROLLBACK")
                return StateCommit(value=None, certainty=CommitCertainty.NOT_COMMITTED, stable_identity=identity)
            connection.execute("COMMIT")
            if self._semantic_unknown_once:
                self._semantic_unknown_once = False
                # Do not expose the pre-commit object: reconcile from durable DB.
                return StateCommit(value=reconcile(), certainty=CommitCertainty.OUTCOME_UNKNOWN, stable_identity=identity)
            return StateCommit(value=value, certainty=CommitCertainty.COMMITTED, stable_identity=identity)
        except CoreStateError:
            connection.execute("ROLLBACK")
            raise
        except sqlite3.DatabaseError as error:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise CoreStateError("STORE_NOT_SERVING") from error

    def _require_serving(self) -> None:
        if not self._serving or self._connection is None:
            raise CoreStateError("STORE_NOT_SERVING")

    def _meta(self, connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            self._serving = False
            raise CoreStateError("STORE_NOT_SERVING")
        return str(row["value"])

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute("INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def _validate_token(self, connection: sqlite3.Connection, token: CoreAuthorityToken) -> None:
        active = self._meta(connection, "active_authority")
        if not active or _load_json(active) != _contract(token) or token.store_authority_id.value != self._meta(connection, "store_authority_id") or token.authority_epoch != int(self._meta(connection, "authority_epoch")):
            raise CoreStateError("STALE_CORE_AUTHORITY")

    def _read_one(self, query: str, params: Sequence[Any], missing: str) -> sqlite3.Row:
        self._require_serving()
        try:
            row = self._connection.execute(query, params).fetchone()
        except sqlite3.DatabaseError as error:
            self._serving = False
            raise CoreStateError("STORE_NOT_SERVING") from error
        if row is None:
            raise CoreStateError(missing)
        return cast(sqlite3.Row, row)

    def _validate_conversation_read(self, conversation_ref: ConversationRef, caller: UserId) -> None:
        row = self._read_one("SELECT payload FROM conversations WHERE conversation_key = ?", (_conversation_key(conversation_ref),), "READ_NOT_AUTHORIZED")
        if ConversationRef.model_validate(_load_json(str(row["payload"]))) != conversation_ref or conversation_ref.user_id != caller:
            raise CoreStateError("READ_NOT_AUTHORIZED")

    def _turn_from_connection(self, connection: sqlite3.Connection, key: str) -> StoredTurn:
        row = connection.execute("SELECT payload FROM turns WHERE turn_key = ?", (key,)).fetchone()
        if row is None:
            raise CoreStateError("TURN_NOT_FOUND")
        return self._decode_turn(_load_json(str(row["payload"])))

    def _response_from_connection(self, connection: sqlite3.Connection, response_id: ResponseId) -> StoredResponse:
        row = connection.execute("SELECT payload FROM responses WHERE response_id = ?", (response_id.value,)).fetchone()
        if row is None:
            raise CoreStateError("RESPONSE_NOT_FOUND")
        return self._decode_response(_load_json(str(row["payload"])))

    def _operation_from_connection(self, connection: sqlite3.Connection, operation_id: OperationId) -> StoredOperation:
        row = connection.execute("SELECT payload FROM operations WHERE operation_id = ?", (operation_id.value,)).fetchone()
        if row is None:
            raise CoreStateError("OPERATION_NOT_FOUND")
        return self._decode_operation(_load_json(str(row["payload"])))

    def _require_open_response(self, connection: sqlite3.Connection, response_id: ResponseId) -> StoredResponse:
        response = self._response_from_connection(connection, response_id)
        if response.state != ResponseState.STARTED:
            raise CoreStateError("RESPONSE_NOT_OPEN")
        turn = self._turn_from_connection(connection, response.turn_key)
        if turn.state in _TERMINAL_TURNS:
            raise CoreStateError("TURN_NOT_OPEN")
        return response

    def _write_turn(self, connection: sqlite3.Connection, key: str, turn: StoredTurn) -> None:
        connection.execute("UPDATE turns SET payload = ? WHERE turn_key = ?", (_json(self._encode_turn(turn)), key))

    def _write_response(self, connection: sqlite3.Connection, response: StoredResponse) -> None:
        connection.execute("UPDATE responses SET payload = ? WHERE response_id = ?", (_json(self._encode_response(response)), response.response_id.value))

    @staticmethod
    def _turn_key_from_turn(turn: StoredTurn) -> str:
        return _turn_key(turn.conversation_ref, turn.turn_ref.turn_id)

    @staticmethod
    def _encode_turn(turn: StoredTurn) -> Dict[str, Any]:
        return {"conversation_ref": _contract(turn.conversation_ref), "turn_ref": _contract(turn.turn_ref), "fingerprint": turn.fingerprint, "snapshot": _contract(turn.snapshot), "turn_input": _contract(turn.turn_input), "revision": turn.revision, "state": turn.state.value, "abort_reason": None if turn.abort_reason is None else turn.abort_reason.value, "response_id": None if turn.response_id is None else turn.response_id.value}

    @staticmethod
    def _decode_turn(payload: Dict[str, Any]) -> StoredTurn:
        input_payload = dict(payload["turn_input"])
        input_payload["parts"] = tuple(input_payload["parts"])
        return StoredTurn(conversation_ref=ConversationRef.model_validate(payload["conversation_ref"]), turn_ref=TurnRef.model_validate(payload["turn_ref"]), fingerprint=str(payload["fingerprint"]), snapshot=TurnSnapshot.model_validate(payload["snapshot"]), turn_input=TurnInput.model_validate(input_payload), revision=int(payload["revision"]), state=TurnState(str(payload["state"])), abort_reason=None if payload["abort_reason"] is None else AbortReason(str(payload["abort_reason"])), response_id=None if payload["response_id"] is None else ResponseId(value=str(payload["response_id"])))

    @staticmethod
    def _encode_response(response: StoredResponse) -> Dict[str, Any]:
        return {"response_id": response.response_id.value, "turn_key": response.turn_key, "state": response.state.value, "revision": response.revision, "segments": [_contract(item) for item in response.segments], "events": [_contract(item) for item in response.events], "attempts": [_contract(item) for item in response.attempts], "abort_reason": None if response.abort_reason is None else response.abort_reason.value}

    @staticmethod
    def _encode_operation(operation: StoredOperation) -> Dict[str, Any]:
        return {"intent": operation.intent.model_dump(mode="json"), "state": operation.state.value, "revision": operation.revision, "not_dispatched_reason": None if operation.not_dispatched_reason is None else operation.not_dispatched_reason.value}

    @staticmethod
    def _decode_operation(payload: Dict[str, Any]) -> StoredOperation:
        # Pydantic's JSON decoder is deliberately used for the frozen contract
        # data only; the surrounding operation ledger envelope is explicit.
        intent = OperationIntentCommitted.model_validate_json(_json(payload["intent"]))
        return StoredOperation(intent=intent, state=DurableOperationState(str(payload["state"])), revision=int(payload["revision"]), not_dispatched_reason=None if payload["not_dispatched_reason"] is None else NotDispatchedReason(str(payload["not_dispatched_reason"])))

    @staticmethod
    def _decode_response(payload: Dict[str, Any]) -> StoredResponse:
        attempts = []
        for item in payload["attempts"]:
            attempt_payload = dict(item)
            attempt_payload["binding_snapshots"] = tuple(attempt_payload["binding_snapshots"])
            started = dict(attempt_payload["started_at"])
            started["value"] = _parse_datetime(started["value"])
            attempt_payload["started_at"] = started
            bindings = []
            for binding in attempt_payload["binding_snapshots"]:
                binding_payload = dict(binding)
                valid_until = dict(binding_payload["authority_valid_until"])
                valid_until["value"] = _parse_datetime(valid_until["value"])
                binding_payload["authority_valid_until"] = valid_until
                bindings.append(binding_payload)
            attempt_payload["binding_snapshots"] = tuple(bindings)
            attempts.append(ExecutionAttemptSnapshot.model_validate(attempt_payload))
        return StoredResponse(response_id=ResponseId(value=str(payload["response_id"])), turn_key=str(payload["turn_key"]), state=ResponseState(str(payload["state"])), revision=int(payload["revision"]), segments=tuple(AgentOutputSegmentCommitted.model_validate(item) for item in payload["segments"]), events=tuple(SQLiteCoreStateStore._decode_event(item) for item in payload["events"]), attempts=tuple(attempts), abort_reason=None if payload["abort_reason"] is None else ResponseAbortReason(str(payload["abort_reason"])))

    @staticmethod
    def _decode_event(payload: Dict[str, Any]) -> ResponseEvent:
        event_type = str(payload["event_type"])
        if event_type == "response_started":
            return ResponseStartedEvent.model_validate(payload)
        if event_type == "segment_committed":
            return SegmentCommittedEvent.model_validate(payload)
        if event_type == "response_finalized":
            return ResponseFinalizedEvent.model_validate(payload)
        if event_type == "response_aborted":
            aborted_payload = dict(payload)
            aborted_payload["reason"] = ResponseAbortReason(str(aborted_payload["reason"]))
            return ResponseAbortedEvent.model_validate(aborted_payload)
        raise CoreStateError("INCOMPATIBLE_FACT_SCHEMA")

    @staticmethod
    def _decode_fact(row: sqlite3.Row, known_id: Optional[FactId] = None) -> FactEnvelope:
        version = int(row["fact_schema_version"])
        if version == 0:
            # v0 had the same explicit fields but lacked the schema marker in
            # payload; this local upcast is deterministic and one-way.
            version = 1
        if version != 1:
            raise CoreStateError("INCOMPATIBLE_FACT_SCHEMA")
        fact_id = known_id or FactId(value=str(row["fact_id"]))
        from yogurt_contracts.foundation.time import UtcInstant
        return FactEnvelope(fact_id=fact_id, fact_type=str(row["fact_type"]), fact_schema_version=version, aggregate_id=str(row["aggregate_id"]), aggregate_revision=int(row["aggregate_revision"]), recorded_at=UtcInstant(value=_parse_datetime(row["recorded_at"])), payload=_load_json(str(row["payload"])))

    @staticmethod
    def _existing_admission(connection: sqlite3.Connection, turn: StoredTurn) -> TurnAdmissionResult:
        if turn.response_id is None:
            return TurnAdmissionResult(status=TurnAdmissionStatus.SAME_LOGICAL_TURN, turn_id=turn.turn_ref.turn_id)
        row = connection.execute("SELECT payload FROM responses WHERE response_id = ?", (turn.response_id.value,)).fetchone()
        if row is None:
            raise CoreStateError("RESPONSE_NOT_FOUND")
        response = SQLiteCoreStateStore._decode_response(_load_json(str(row["payload"])))
        mapping = {ResponseState.STARTED: AdmissionResponseState.STARTED, ResponseState.FINALIZED: AdmissionResponseState.FINALIZED, ResponseState.ABORTED: AdmissionResponseState.ABORTED}
        return TurnAdmissionResult(status=TurnAdmissionStatus.SAME_LOGICAL_TURN, turn_id=turn.turn_ref.turn_id, response_id=turn.response_id, response_state=mapping[response.state])
