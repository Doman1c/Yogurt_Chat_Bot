from dataclasses import dataclass, field
from typing import Optional, Tuple

from yogurt_contracts.conversation.input import TurnInput
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import ExecutionAttemptId, ResponseId
from yogurt_contracts.response.lifecycle import ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.response.events import ResponseEvent


@dataclass(frozen=True)
class ExecutionAttemptSnapshot:
    execution_attempt_id: ExecutionAttemptId
    response_id: ResponseId
    attempt_number: int
    model_binding: str
    provider_generation: str
    provider_config_revision: str


@dataclass(frozen=True)
class ModelContext:
    persona_material: str
    history: Tuple[str, ...]
    user_input: str
    turn_origin_surface: str
    context_budget: int


@dataclass(frozen=True)
class StoredTurn:
    conversation_ref: ConversationRef
    turn_ref: TurnRef
    fingerprint: str
    snapshot: TurnSnapshot
    turn_input: TurnInput
    revision: int = 1
    terminal_reason: Optional[str] = None
    response_id: Optional[ResponseId] = None


@dataclass(frozen=True)
class StoredResponse:
    response_id: ResponseId
    turn_key: str
    state: ResponseState
    revision: int
    segments: Tuple[AgentOutputSegmentCommitted, ...] = ()
    events: Tuple[ResponseEvent, ...] = ()
    attempts: Tuple[ExecutionAttemptSnapshot, ...] = ()
