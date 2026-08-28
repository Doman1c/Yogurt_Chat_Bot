from ..conversation.refs import TurnOrigin
from ..foundation.model import FrozenContract


class TurnSnapshot(FrozenContract):
    identity_revision: str
    core_config_revision: str
    turn_origin: TurnOrigin
    conversation_context_revision: str
    principal_binding_reference: str
