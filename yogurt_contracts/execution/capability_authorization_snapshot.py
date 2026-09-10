"""Operation-neutral capability authorization captured at an invocation boundary."""

from pydantic import model_validator

from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..identity.authority import AuthorityClaims


class CapabilityAuthorizationSnapshot(FrozenContract):
    """A bounded authorization decision reusable by both read and write capabilities."""

    claims: AuthorityClaims
    authorization_revision: str
    checked_at: UtcInstant
    valid_until: UtcInstant

    @model_validator(mode="after")
    def has_bounded_validity(self) -> "CapabilityAuthorizationSnapshot":
        if not self.authorization_revision or len(self.authorization_revision) > 256:
            raise ValueError("authorization_revision must be non-empty and bounded")
        if self.valid_until.value < self.checked_at.value:
            raise ValueError("authorization snapshot valid_until must not precede checked_at")
        if self.valid_until.value > self.claims.valid_until.value:
            raise ValueError("authorization snapshot cannot outlive its authority claims")
        return self
