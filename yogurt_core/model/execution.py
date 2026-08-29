from dataclasses import dataclass
from typing import Optional, Tuple

from yogurt_contracts.conversation.input import TurnInput
from yogurt_contracts.conversation.lifecycle import AbortReason, TurnState
from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_contracts.execution.turn_snapshot import TurnSnapshot
from yogurt_contracts.foundation.identifiers import ResponseId
from yogurt_contracts.response.lifecycle import ResponseState
from yogurt_contracts.response.models import AgentOutputSegmentCommitted
from yogurt_contracts.response.events import ResponseEvent


@dataclass(frozen=True)
class ModelContext:
    persona_material: str
    history: Tuple["ContextEntry", ...]
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
    state: TurnState = TurnState.INPUT_COMMITTED
    abort_reason: Optional[AbortReason] = None
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


# Imported at the end to keep ContextEntry's provider-neutral type separate from
# response state while avoiding an application-layer circular import.
from yogurt_core.context.models import ContextEntry  # noqa: E402
