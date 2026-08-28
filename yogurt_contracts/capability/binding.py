from typing import Optional

from ..foundation.identifiers import BindingId, CapabilityId, RuntimeInstanceId, RuntimeSlotId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..foundation.versioning import ContractVersion


class CapabilityBinding(FrozenContract):
    binding_id: BindingId
    binding_revision: str
    capability_id: CapabilityId
    provider_slot_id: RuntimeSlotId
    provider_instance_id: RuntimeInstanceId
    provider_generation: int
    contract_version: ContractVersion
    authority_valid_until: UtcInstant
    endpoint_reference: Optional[str] = None
