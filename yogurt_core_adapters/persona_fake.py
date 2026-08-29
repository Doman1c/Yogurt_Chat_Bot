from typing import Dict

from yogurt_contracts.execution.persona import PersonaSnapshot
from yogurt_contracts.foundation.identifiers import PersonaId


class FakePersonaSnapshotPort:
    def __init__(self, snapshots: Dict[str, PersonaSnapshot]) -> None:
        self._snapshots = snapshots

    async def get_snapshot(self, persona_id: PersonaId) -> PersonaSnapshot:
        return self._snapshots[persona_id.value]

    def replace(self, snapshot: PersonaSnapshot) -> None:
        self._snapshots[snapshot.persona_id.value] = snapshot
