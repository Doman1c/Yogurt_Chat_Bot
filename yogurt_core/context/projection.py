from yogurt_core.context.models import ContextProjection
from yogurt_core.model.execution import StoredTurn
from yogurt_core.ports.state_store import CoreStateStore


class ContextProjectionService:
    """Read model only. It never becomes state authority."""

    def __init__(self, store: CoreStateStore) -> None:
        self._store = store

    async def project(self, turn: StoredTurn) -> ContextProjection:
        history = await self._store.get_conversation_context(turn.conversation_ref, turn.turn_ref.turn_id)
        current = tuple(part.text for part in turn.turn_input.parts if hasattr(part, "text"))
        return ContextProjection(
            committed_user_inputs=history[0] + current,
            committed_assistant_segments=history[1],
            conversation_metadata=turn.snapshot.conversation_context_revision,
        )
