from typing import Protocol

from yogurt_contracts.execution.persona import PersonaSnapshot
from yogurt_contracts.foundation.identifiers import PersonaId


class PersonaSnapshotPort(Protocol):
    async def get_snapshot(self, persona_id: PersonaId) -> PersonaSnapshot:
        ...
