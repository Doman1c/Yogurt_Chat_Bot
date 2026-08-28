"""Cross-runtime UTC time contracts; monotonic clocks are process-local only."""

from datetime import datetime, timedelta, timezone

from pydantic import field_validator

from .model import FrozenContract


class UtcInstant(FrozenContract):
    value: datetime

    @field_validator("value")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UtcInstant requires a timezone-aware datetime")
        if value.utcoffset() != timedelta(0):
            raise ValueError("UtcInstant requires UTC (+00:00)")
        return value.astimezone(timezone.utc)


class Duration(FrozenContract):
    milliseconds: int

    @field_validator("milliseconds")
    @classmethod
    def non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("duration must be non-negative")
        return value


class Deadline(FrozenContract):
    expires_at: UtcInstant
