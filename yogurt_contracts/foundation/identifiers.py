"""Opaque, strongly typed identifiers and their normative generation authorities."""

from __future__ import annotations

from typing import ClassVar

from pydantic import field_validator

from .model import FrozenContract


class Identifier(FrozenContract):
    """A bounded opaque identifier. Its value is not a routing namespace."""

    value: str
    generation_authority: ClassVar[str] = "unspecified"

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if not value or len(value) > 256:
            raise ValueError("identifier must be non-empty and at most 256 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("identifier must not contain control characters")
        return value

    def __str__(self) -> str:
        return self.value


class PersonaId(Identifier): generation_authority = "Persona Authority"
class UserId(Identifier): generation_authority = "Identity Resolver"
class ConversationId(Identifier): generation_authority = "Core / Conversation Authority"
class TurnId(Identifier): generation_authority = "Trusted Ingress or Core Admission Authority"
class InputId(Identifier): generation_authority = "Trusted Ingress"
class ResponseId(Identifier): generation_authority = "Core"
class SegmentId(Identifier): generation_authority = "Core"
class ExecutionAttemptId(Identifier): generation_authority = "Core Execution Authority"
class OperationId(Identifier): generation_authority = "Operation Intent Authority"
class OperationAttemptId(Identifier): generation_authority = "Operation Dispatcher"
class ReceiptId(Identifier): generation_authority = "Presentation Provider"
class RuntimeSlotId(Identifier): generation_authority = "Runtime Deployment Authority"
class RuntimeInstanceId(Identifier): generation_authority = "Runtime Instance"
class NodeId(Identifier): generation_authority = "Node Authority"
class BindingId(Identifier): generation_authority = "Capability Binding Authority"
class LeaseId(Identifier): generation_authority = "Lease Issuer"
class TraceId(Identifier): generation_authority = "Observability Layer"
class CapabilityId(Identifier): generation_authority = "Capability Contract Authority"
class ExternalPrincipalRef(Identifier): generation_authority = "Trusted Surface Authentication"
class IdentityResolutionRef(Identifier): generation_authority = "Identity Resolver"
class HypothesisId(Identifier): generation_authority = "STT Runtime"
class SurfaceId(Identifier): generation_authority = "Surface Authority"
class GenerationAuthorityId(Identifier): generation_authority = "Management Generation Authority"
class CoreInstanceId(Identifier): generation_authority = "Yogurt Core Instance"
class StoreAuthorityId(Identifier): generation_authority = "Core State Store"
class ResponseEventId(Identifier): generation_authority = "Core Response Event Stream"
