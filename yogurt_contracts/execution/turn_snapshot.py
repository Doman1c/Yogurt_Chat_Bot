from typing import Optional

from ..conversation.refs import TurnOrigin
from ..foundation.model import FrozenContract
from .persona import PersonaSnapshot


class TurnSnapshot(FrozenContract):
    identity_revision: str
    core_config_revision: str
    turn_origin: TurnOrigin
    conversation_context_revision: str
    principal_binding_reference: str
    # R1 callers remain valid; R2-A admissions always populate this immutable view.
    persona_snapshot: Optional[PersonaSnapshot] = None
