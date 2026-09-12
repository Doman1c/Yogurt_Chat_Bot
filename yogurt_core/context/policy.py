import json
from dataclasses import replace
from typing import TYPE_CHECKING, Dict, List, Optional

from yogurt_contracts.conversation.working_state import ConversationWorkingState, WorkingStateLifecycle
from yogurt_contracts.conversation.refs import TurnRef
from yogurt_core.context.material import WorkingStateContextMaterial
from yogurt_core.context.models import ContextProjection
from yogurt_core.model.execution import ModelContext, StoredTurn

if TYPE_CHECKING:
    from yogurt_core.context.composition import ContextCompositionInputs


WORKING_STATE_CHARACTER_BUDGET = 1200


def working_state_material(state: ConversationWorkingState, current: TurnRef) -> Optional[WorkingStateContextMaterial]:
    """Admit whole values in W1 order: questions, threads, focus. No truncation."""
    if state.derived_through_turn != current:
        raise ValueError("WORKING_STATE_CONTEXT_BOUNDARY_MISMATCH")
    selected: Dict[str, List[str]] = {"active_threads": [], "recent_focus": [], "unresolved_items": []}

    def canonical() -> str:
        return json.dumps({"kind": "conversation_working_state", "authority": "user_derived_context_data",
                           "instructional": False, **selected}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    for name, entries, limit in (("unresolved_items", state.unresolved_items, 3),
                                 ("active_threads", state.active_threads, 3),
                                 ("recent_focus", state.recent_focus, 2)):
        candidates = tuple(entry for entry in entries
                           if entry.lifecycle == WorkingStateLifecycle.ACTIVE
                           and (any(ref != current for ref in entry.source_turn_refs)
                                if name == "active_threads" else current not in entry.source_turn_refs))[:limit]
        for entry in candidates:
            selected[name].append(entry.value)
            if len(canonical()) > WORKING_STATE_CHARACTER_BUDGET:
                selected[name].pop()
    if not any(selected.values()):
        return None
    return WorkingStateContextMaterial(state.derivation_version, state.revision, current, canonical())


class ContextBudgetPolicy:
    """Deterministic R2-A truncation owned by Core, never the model adapter."""

    def __init__(self, budget: int = 32) -> None:
        self._budget = budget

    def compose_inputs(self, inputs: "ContextCompositionInputs") -> ModelContext:
        if inputs.working_state.conversation_ref != inputs.turn.conversation_ref:
            raise ValueError("WORKING_STATE_CONTEXT_CONVERSATION_MISMATCH")
        context = self.compose(inputs.projection, inputs.turn)
        return replace(context, working_state=working_state_material(inputs.working_state, inputs.turn.turn_ref))

    def compose(self, projection: ContextProjection, turn: StoredTurn) -> ModelContext:
        persona = turn.snapshot.persona_snapshot
        if persona is None:
            raise ValueError("R2-A turn execution requires a frozen PersonaSnapshot")
        history = projection.entries[-self._budget:]
        input_text = "\n".join(part.text for part in turn.turn_input.parts if hasattr(part, "text"))
        return ModelContext(
            persona_material=persona.model_context_material,
            history=history,
            user_input=input_text,
            turn_origin_surface=turn.snapshot.turn_origin.origin_surface,
            context_budget=self._budget,
        )
