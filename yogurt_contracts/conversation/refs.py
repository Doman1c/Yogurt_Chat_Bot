from typing import Optional

from ..foundation.identifiers import ConversationId, ExternalPrincipalRef, IdentityResolutionRef, PersonaId, TurnId, UserId
from ..foundation.model import FrozenContract


class ConversationRef(FrozenContract):
    persona_id: PersonaId
    user_id: UserId
    conversation_id: ConversationId


class TurnRef(FrozenContract):
    conversation_id: ConversationId
    turn_id: TurnId
    parent_turn_id: Optional[TurnId] = None
    sequence: Optional[int] = None


class TurnOrigin(FrozenContract):
    origin_surface: str
    external_principal_ref: ExternalPrincipalRef
    identity_resolution_ref: IdentityResolutionRef
