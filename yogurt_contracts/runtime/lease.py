from ..foundation.identifiers import LeaseId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from .identity import RuntimeIdentity


class RuntimeLease(FrozenContract):
    lease_id: LeaseId
    runtime: RuntimeIdentity
    issued_at: UtcInstant
    expires_at: UtcInstant
    issuer: str
