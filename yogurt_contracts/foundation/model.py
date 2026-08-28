"""Private helpers for the Python reference binding, not a wire schema."""

from pydantic import BaseModel, ConfigDict


class FrozenContract(BaseModel):
    """Strict, deeply immutable-by-construction contract base class."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
