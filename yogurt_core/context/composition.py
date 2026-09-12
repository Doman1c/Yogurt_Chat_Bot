"""Core-owned input assembly, once per logical response (not per attempt)."""

from dataclasses import dataclass

from yogurt_contracts.conversation.working_state import ConversationWorkingState
from yogurt_core.context.models import ContextProjection
from yogurt_core.context.policy import ContextBudgetPolicy
from yogurt_core.context.projection import ContextProjectionService
from yogurt_core.context.working_state import WorkingStateProjectionService
from yogurt_core.model.execution import ModelContext, StoredTurn
from yogurt_core.ports.state_store import CoreStateError, CoreStateStore


@dataclass(frozen=True)
class ContextCompositionInputs:
    projection: ContextProjection
    working_state: ConversationWorkingState
    turn: StoredTurn


class ContextComposer:
    def __init__(self, store: CoreStateStore, policy: ContextBudgetPolicy) -> None:
        self._projection = ContextProjectionService(store)
        self._working_state = WorkingStateProjectionService(store)
        self._policy = policy

    async def compose(self, turn: StoredTurn) -> ModelContext:
        projection = await self._projection.project(turn)
        try:
            state = await self._working_state.project(turn.conversation_ref, turn.turn_ref)
        except CoreStateError as error:
            if error.code == "STALE_CORE_AUTHORITY":
                raise
            # A cognition read failure must follow Core's execution-failure
            # convergence, not its write-conflict/fencing fast exit.
            raise RuntimeError("WORKING_STATE_PROJECTION_FAILED") from error
        return self._policy.compose_inputs(ContextCompositionInputs(projection, state, turn))
