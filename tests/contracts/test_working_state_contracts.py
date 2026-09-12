import pytest
from pydantic import ValidationError

from yogurt_contracts.conversation.refs import ConversationRef, TurnRef
from yogurt_contracts.conversation.working_state import ConversationWorkingState, WorkingStateEntry, WorkingStateKind, WorkingStateLifecycle
from yogurt_contracts.foundation.identifiers import ConversationId, PersonaId, UserId, TurnId


def test_strict_frozen_working_state_contract_bounds_and_invariants():
    ref = ConversationRef(persona_id=PersonaId(value="p"), user_id=UserId(value="u"), conversation_id=ConversationId(value="c"))
    turn = TurnRef(conversation_id=ref.conversation_id, turn_id=TurnId(value="t"))
    fields = dict(entry_id="id", kind=WorkingStateKind.FOCUS, value="focus", source_turn_refs=(turn,), derived_revision=2, lifecycle=WorkingStateLifecycle.ACTIVE)
    entry = WorkingStateEntry(**fields)
    for update in ({"value": "x" * 161}, {"source_turn_refs": []}, {"source_turn_refs": (turn,) * 4}, {"derived_revision": "2"}, {"user_mood": "happy"}, {"value": " "}):
        with pytest.raises(ValidationError):
            WorkingStateEntry(**dict(fields, **update))
    with pytest.raises(ValidationError):
        entry.value = "mutation"
    state_fields = dict(conversation_ref=ref, revision=2, derivation_version="working-state-v1", derived_through_turn=turn, recent_focus=(entry,))
    state = ConversationWorkingState(**state_fields)
    assert ConversationWorkingState.model_validate_json(state.model_dump_json()) == state
    for update in ({"recent_focus": (entry,) * 4}, {"derived_through_turn": None}, {"revision": 1}, {"active_threads": (entry,)}, {"recent_focus": [entry]}):
        with pytest.raises(ValidationError):
            ConversationWorkingState(**dict(state_fields, **update))
