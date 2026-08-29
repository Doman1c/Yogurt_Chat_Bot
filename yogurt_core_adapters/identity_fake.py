from typing import Dict

from yogurt_contracts.foundation.identifiers import ExternalPrincipalRef
from yogurt_contracts.identity.principal import ExternalPrincipal
from yogurt_contracts.identity.resolution import IdentityResolution


class FakeIdentityResolver:
    def __init__(self, resolutions: Dict[str, IdentityResolution]) -> None:
        self._resolutions = resolutions

    async def resolve(self, principal: ExternalPrincipal) -> IdentityResolution:
        try:
            return self._resolutions[principal.reference.value]
        except KeyError:
            raise ValueError("unknown external principal")
