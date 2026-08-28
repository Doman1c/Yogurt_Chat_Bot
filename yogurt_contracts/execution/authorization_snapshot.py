from pydantic import model_validator

from ..foundation.identifiers import OperationId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..identity.authority import AuthorityClaims


class OperationAuthorizationSnapshot(FrozenContract):
    operation_id: OperationId
    claims: AuthorityClaims
    checked_at: UtcInstant
    valid_until: UtcInstant

    @model_validator(mode="after")
    def has_bounded_validity(self) -> "OperationAuthorizationSnapshot":
        if self.valid_until.value < self.checked_at.value:
            raise ValueError("authorization snapshot valid_until must not precede checked_at")
        if self.valid_until.value > self.claims.valid_until.value:
            raise ValueError("authorization snapshot cannot outlive its authority claims")
        return self
from pydantic import model_validator
