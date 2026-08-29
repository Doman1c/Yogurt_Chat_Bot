"""Explicit versioned envelopes for durable Core facts.

The envelope is the persistence boundary.  Its payload is deliberately a plain
JSON object rather than a serialized Pydantic model, so model implementation
details are never the durable ABI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict

from pydantic import field_validator

from .identifiers import FactId
from .model import FrozenContract
from .time import UtcInstant


class CommitCertainty(str, Enum):
    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FactEnvelope(FrozenContract):
    """One append-only, independently idempotent durable state fact."""

    fact_id: FactId
    fact_type: str
    fact_schema_version: int
    aggregate_id: str
    aggregate_revision: int
    recorded_at: UtcInstant
    payload: Dict[str, Any]

    @field_validator("fact_type", "aggregate_id")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("fact identity fields must be non-empty")
        return value

    @field_validator("fact_schema_version", "aggregate_revision")
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("fact schema version and revision must be positive")
        return value


class FactCommitResult(FrozenContract):
    fact_id: FactId
    certainty: CommitCertainty
    aggregate_revision: int


def utc_now() -> UtcInstant:
    """Small explicit clock seam used when an adapter creates internal facts."""

    return UtcInstant(value=datetime.now(timezone.utc))
