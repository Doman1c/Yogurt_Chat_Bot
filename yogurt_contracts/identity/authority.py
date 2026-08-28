from typing import Optional

from ..foundation.identifiers import CapabilityId, OperationId, RuntimeInstanceId, UserId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant


class AuthorityClaims(FrozenContract):
    actor: RuntimeInstanceId
    subject: UserId
    origin: str
    audience: RuntimeInstanceId
    scope: CapabilityId
    grant_revision: str
    valid_until: UtcInstant
    authority_reference: str


class DelegatedAuthority(FrozenContract):
    claims: AuthorityClaims
    operation_id: Optional[OperationId] = None
