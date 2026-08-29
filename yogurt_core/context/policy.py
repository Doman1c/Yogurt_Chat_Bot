from yogurt_contracts.execution.persona import PersonaSnapshot

from yogurt_core.context.models import ContextProjection
from yogurt_core.model.execution import ModelContext, StoredTurn


class ContextBudgetPolicy:
    """Deterministic R2-A truncation owned by Core, never the model adapter."""

    def __init__(self, budget: int = 32) -> None:
        self._budget = budget

    def compose(self, projection: ContextProjection, turn: StoredTurn) -> ModelContext:
        persona = turn.snapshot.persona_snapshot
        if persona is None:
            raise ValueError("R2-A turn execution requires a frozen PersonaSnapshot")
        history = projection.committed_user_inputs + projection.committed_assistant_segments
        history = history[-self._budget:]
        input_text = "\n".join(part.text for part in turn.turn_input.parts if hasattr(part, "text"))
        return ModelContext(
            persona_material=persona.model_context_material,
            history=history,
            user_input=input_text,
            turn_origin_surface=turn.snapshot.turn_origin.origin_surface,
            context_budget=self._budget,
        )
