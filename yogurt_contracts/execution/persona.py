"""Immutable persona material frozen at the Turn admission boundary."""

from ..foundation.identifiers import PersonaId
from ..foundation.model import FrozenContract


class PersonaSnapshot(FrozenContract):
    persona_id: PersonaId
    persona_revision: str
    soul_revision: str
    identity_revision: str
    relationship_context_revision: str
    model_context_material: str
