from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .errors import SingingErrorCode


SingingEventKind = Literal[
    "queued",
    "asset_ready",
    "separation_started",
    "separation_finished",
    "conversion_started",
    "conversion_finished",
    "remix_started",
    "cover_ready",
    "failed",
    "cancelled",
]


class SingingEvent(BaseModel):
    """Bounded lifecycle event without lyrics, paths, engine arguments, or errors."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=64)
    kind: SingingEventKind
    timestamp_ms: int = Field(ge=0)
    error_code: Optional[SingingErrorCode] = None
