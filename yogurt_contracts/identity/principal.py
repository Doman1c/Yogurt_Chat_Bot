from typing import Optional

from ..foundation.identifiers import ExternalPrincipalRef, RuntimeInstanceId, UserId
from ..foundation.model import FrozenContract


class ExternalPrincipal(FrozenContract):
    reference: ExternalPrincipalRef
    authenticated_surface: str


class CallerIdentity(FrozenContract):
    runtime_instance_id: RuntimeInstanceId
    runtime_type: str


class LogicalUser(FrozenContract):
    user_id: UserId


class PersonaRef(FrozenContract):
    persona_id: str
    revision: Optional[str] = None
