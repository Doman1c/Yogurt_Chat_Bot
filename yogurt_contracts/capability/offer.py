from typing import Generic, Optional

from pydantic import field_validator

from ..foundation.identifiers import CapabilityId, RuntimeInstanceId, RuntimeSlotId
from ..foundation.model import FrozenContract
from ..foundation.time import UtcInstant
from ..foundation.versioning import ContractVersion
from .framework import BaseCapabilityDescriptor, CapabilityState, DescriptorT


class CapabilityOffer(FrozenContract, Generic[DescriptorT]):
    capability_id: CapabilityId
    contract_version: ContractVersion
    provider_slot_id: RuntimeSlotId
    provider_instance_id: RuntimeInstanceId
    provider_generation: int
    descriptor: DescriptorT
    state: CapabilityState
    lease_expires_at: UtcInstant
    endpoint_reference: Optional[str] = None

    @field_validator("provider_generation")
    @classmethod
    def has_positive_generation(cls, value: int) -> int:
        if value < 1:
            raise ValueError("provider_generation must be at least 1")
        return value
