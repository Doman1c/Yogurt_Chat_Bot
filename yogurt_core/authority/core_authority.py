from yogurt_contracts.foundation.identifiers import CoreInstanceId
from yogurt_contracts.runtime.core_authority import CoreAuthorityToken

from yogurt_core.ports.state_store import CoreStateStore


class CoreAuthorityService:
    """Small boundary around store-issued core fencing tokens."""

    def __init__(self, store: CoreStateStore, core_instance_id: CoreInstanceId) -> None:
        self._store = store
        self._core_instance_id = core_instance_id

    async def acquire(self) -> CoreAuthorityToken:
        return await self._store.acquire_authority(self._core_instance_id)

    async def release(self, token: CoreAuthorityToken) -> None:
        await self._store.release_authority(token)
