from enum import Enum

from .identifiers import Identifier


class RequestFingerprint(Identifier):
    """Sensitive SHA-256 digest metadata, not anonymization or telemetry data."""
    generation_authority = "Contract Canonicalization"


class IdempotencyDecision(str, Enum):
    SAME_LOGICAL_REQUEST = "same_logical_request"
    CONFLICT = "conflict"
