from typing import Protocol

from yogurt_contracts.identity.principal import ExternalPrincipal
from yogurt_contracts.identity.resolution import IdentityResolution


class IdentityResolver(Protocol):
    async def resolve(self, principal: ExternalPrincipal) -> IdentityResolution:
        ...
