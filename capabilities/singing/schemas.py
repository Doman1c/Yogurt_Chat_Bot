from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .errors import SingingErrorCode


AssetId = str
SingingProfile = Literal["yogurt"]
CoverOutput = Literal["mixed", "vocal"]
ExpressionPreservation = Literal["low", "medium", "high"]


class CoverRequest(BaseModel):
    """Public, bounded cover request; the caller never supplies a path or engine."""

    model_config = ConfigDict(extra="forbid")

    asset_id: AssetId = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    singing_profile: SingingProfile = "yogurt"
    transpose_semitones: int = Field(default=0, ge=-12, le=12)
    preserve_pitch: bool = True
    preserve_expression: bool = True
    expression_preservation: ExpressionPreservation = "medium"
    output: CoverOutput = "mixed"
    vocal_gain_db: float = Field(default=0.0, ge=-18.0, le=12.0)
    instrumental_gain_db: float = Field(default=0.0, ge=-18.0, le=12.0)
    normalize_peak: bool = True


class SingingResult(BaseModel):
    """Safe result with in-memory audio only; local asset paths never escape."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=64)
    success: bool
    output: CoverOutput
    audio: Optional[bytes] = None
    media_type: Optional[Literal["audio/wav"]] = None
    duration_ms: Optional[int] = Field(default=None, ge=0)
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=192000)
    latency_ms: Optional[int] = Field(default=None, ge=0)
    error_code: Optional[SingingErrorCode] = None


class BakeoffRecord(BaseModel):
    """Portable, non-sensitive record for comparing locally run SVC engines."""

    model_config = ConfigDict(extra="forbid")

    engine: str = Field(min_length=1, max_length=32)
    engine_version: str = Field(min_length=1, max_length=64)
    sample_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    language: Literal["zh", "ja", "en"]
    transpose_semitones: int = Field(ge=-12, le=12)
    wall_time_ms: int = Field(ge=0)
    output_duration_ms: Optional[int] = Field(default=None, ge=0)
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=192000)
    success: bool
    error_code: Optional[SingingErrorCode] = None


class BakeoffRunManifest(BaseModel):
    """Non-sensitive, machine-readable record of one local bake-off invocation."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    engine: str = Field(min_length=1, max_length=32)
    engine_revision: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    profile_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9._-]{0,63}$")
    sample_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    language: Literal["zh", "ja", "en"]
    source_duration_ms: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)
    output_duration_ms: Optional[int] = Field(default=None, ge=0)
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=192000)
    transpose_semitones: int = Field(ge=-12, le=12)
    peak_ram_mib: Optional[float] = Field(default=None, ge=0)
    peak_vram_mib: Optional[float] = Field(default=None, ge=0)
    f0_correlation: Optional[float] = Field(default=None, ge=-1, le=1)
    f0_rmse_cents: Optional[float] = Field(default=None, ge=0)
    voiced_unvoiced_agreement: Optional[float] = Field(default=None, ge=0, le=1)
    duration_ratio: Optional[float] = Field(default=None, gt=0)
    success: bool
    error_code: Optional[SingingErrorCode] = None
