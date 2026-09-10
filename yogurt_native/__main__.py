"""Run with python -m yogurt_native; all secrets/config supplied server-side."""
import os
from pathlib import Path
import uvicorn
from yogurt_contracts.execution.persona import PersonaSnapshot
from yogurt_contracts.foundation.identifiers import CoreInstanceId, ExternalPrincipalRef, IdentityResolutionRef, PersonaId, UserId
from yogurt_core.application.core import YogurtCore
from yogurt_core_adapters.state_store_sqlite import SQLiteCoreStateStore
from .gateway import NativeGateway
from .http import create_app
from .identity import Credential, OwnerProfile, ServerIdentityMapping
from .model import LocalOllamaModel
from .telemetry import LatencyTelemetry

class ConfiguredPersona:
    def __init__(self, snapshot: PersonaSnapshot) -> None:
        self.snapshot = snapshot

    async def get_snapshot(self, persona_id: PersonaId) -> PersonaSnapshot:
        if persona_id != self.snapshot.persona_id:
            raise ValueError("unmapped persona")
        return self.snapshot

def main() -> None:
    profile = OwnerProfile(PersonaId(value=os.environ.get("YOGURT_NATIVE_PERSONA", "yogurt")),
        UserId(value=os.environ.get("YOGURT_NATIVE_USER", "owner")),
        ExternalPrincipalRef(value="native-owner"), IdentityResolutionRef(value="native-owner-v1"))
    identities = ServerIdentityMapping((Credential(os.environ["YOGURT_NATIVE_TOKEN"], profile),))
    mapping_key = os.environ["YOGURT_NATIVE_MAPPING_KEY"].encode()
    persona = PersonaSnapshot(persona_id=profile.persona_id, persona_revision="native-1", soul_revision="native-1",
        identity_revision="native-1", relationship_context_revision="native-1",
        model_context_material=Path(os.environ["YOGURT_NATIVE_PERSONA_FILE"]).read_text(encoding="utf-8"))
    telemetry = LatencyTelemetry()
    core = YogurtCore(SQLiteCoreStateStore(os.environ["YOGURT_NATIVE_DATABASE"]), identities,
        ConfiguredPersona(persona),
        LocalOllamaModel(os.environ["YOGURT_NATIVE_MODEL"]), CoreInstanceId(value="native-local"), observer=telemetry)
    app = create_app(NativeGateway(core, mapping_key, telemetry), identities)
    uvicorn.run(app, host="127.0.0.1", port=17622, access_log=False)

if __name__ == "__main__":
    main()
