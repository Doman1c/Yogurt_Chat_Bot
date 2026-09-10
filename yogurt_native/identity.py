"""Server-configured authenticated identity mapping, shared across devices."""
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Optional, Tuple
from yogurt_contracts.foundation.identifiers import ExternalPrincipalRef, IdentityResolutionRef, PersonaId, UserId
from yogurt_contracts.identity.principal import ExternalPrincipal
from yogurt_contracts.identity.resolution import IdentityResolution

@dataclass(frozen=True)
class OwnerProfile:
    persona_id: PersonaId
    user_id: UserId
    principal_ref: ExternalPrincipalRef
    resolution_ref: IdentityResolutionRef

@dataclass(frozen=True)
class Credential:
    token: str = field(repr=False)
    profile: OwnerProfile

class ServerIdentityMapping:
    def __init__(self, credentials: Tuple[Credential, ...]) -> None:
        if not credentials or any(len(item.token) < 32 for item in credentials):
            raise ValueError("configure bearer tokens of at least 32 characters")
        self._credentials = credentials
        profiles = {item.profile.principal_ref.value: item.profile for item in credentials}
        if len(profiles) != len({item.profile for item in credentials}):
            raise ValueError("ambiguous principal mapping")
        if len({item.token for item in credentials}) != len(credentials):
            raise ValueError("duplicate bearer token")
        self._profiles = profiles

    def authenticate(self, authorization: str) -> Optional[OwnerProfile]:
        supplied = hashlib.sha256(authorization.encode()).digest()
        found = None
        for item in self._credentials:
            expected = hashlib.sha256(("Bearer " + item.token).encode()).digest()
            if hmac.compare_digest(supplied, expected):
                found = item.profile
        return found

    async def resolve(self, principal: ExternalPrincipal) -> IdentityResolution:
        profile = self._profiles.get(principal.reference.value)
        if profile is None or principal.authenticated_surface != "native":
            raise ValueError("unmapped principal")
        return IdentityResolution(reference=profile.resolution_ref, logical_user=profile.user_id,
                                  identity_revision="native-1", source_hash="server-configured")
