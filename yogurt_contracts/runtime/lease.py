from ..foundation.identifiers import LeaseId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from pydantic import model_validator
from .identity import RuntimeIdentity


class RuntimeLease(FrozenContract):
    lease_id: LeaseId
    runtime: RuntimeIdentity
    issued_at: UtcInstant
    expires_at: UtcInstant
    issuer: str

    @model_validator(mode="after")
    def expires_after_issue(self) -> "RuntimeLease":
        if self.expires_at.value <= self.issued_at.value:
            raise ValueError("runtime lease expires_at must follow issued_at")
        return self
