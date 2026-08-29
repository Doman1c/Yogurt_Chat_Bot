from yogurt_core.context.models import ContextProjection
from yogurt_core.model.execution import StoredTurn
from yogurt_core.ports.state_store import CoreStateStore


class ContextProjectionService:
    """Read model only. It never becomes state authority."""

    def __init__(self, store: CoreStateStore) -> None:
        self._store = store

    async def project(self, turn: StoredTurn) -> ContextProjection:
        entries = await self._store.get_conversation_context(turn.conversation_ref, turn.turn_ref.turn_id)
        return ContextProjection(
            entries=entries,
            conversation_metadata=turn.snapshot.conversation_context_revision,
        )
