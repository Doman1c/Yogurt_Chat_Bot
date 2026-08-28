from pydantic import field_validator

from .model import FrozenContract


class _Version(FrozenContract):
    major: int
    minor: int

    @field_validator("major", "minor")
    @classmethod
    def non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("version components must be non-negative")
        return value


class ContractVersion(_Version):
    """Major is semantic breaking; minor is additive compatible evolution."""


class RuntimeVersion(_Version):
    """Software runtime version, separate from ContractVersion."""
