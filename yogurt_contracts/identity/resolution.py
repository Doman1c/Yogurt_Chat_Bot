from ..foundation.identifiers import IdentityResolutionRef, UserId
from ..foundation.model import FrozenContract


class IdentityResolution(FrozenContract):
    reference: IdentityResolutionRef
    logical_user: UserId
    identity_revision: str
    source_hash: str
