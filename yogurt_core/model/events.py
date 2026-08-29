"""Sanitized, provider-neutral model draft events."""

from enum import Enum
from typing import Union

from pydantic import field_validator

from yogurt_contracts.foundation.model import FrozenContract


class VisibleTextDelta(FrozenContract):
    text: str
    language: str = "und"

    @field_validator("text")
    @classmethod
    def visible_text(cls, value: str) -> str:
        if not value:
            raise ValueError("visible text must be non-empty")
        return value


class StructuredControlDelta(FrozenContract):
    """Already-parsed non-visible control data; never committed to response text."""
    control_type: str


class UsageObservation(FrozenContract):
    input_tokens: int = 0
    output_tokens: int = 0


class AttemptTerminalKind(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptTerminal(FrozenContract):
    kind: AttemptTerminalKind
    reason: str = ""


ModelDraftEvent = Union[VisibleTextDelta, StructuredControlDelta, UsageObservation, AttemptTerminal]
