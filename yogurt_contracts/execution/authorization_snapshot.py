from ..foundation.identifiers import OperationId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..identity.authority import AuthorityClaims


class OperationAuthorizationSnapshot(FrozenContract):
    operation_id: OperationId
    claims: AuthorityClaims
    checked_at: UtcInstant
    valid_until: UtcInstant
