"""Fencing token for authoritative R2 core state transitions."""

from ..foundation.identifiers import CoreInstanceId, StoreAuthorityId
from ..foundation.model import FrozenContract


class CoreAuthorityOwnership(FrozenContract):
    core_instance_id: CoreInstanceId
    store_authority_id: StoreAuthorityId
    authority_epoch: int


class CoreAuthorityToken(CoreAuthorityOwnership):
    """Unforgeable-by-comparison token issued by the authoritative state store."""

    token_nonce: str
