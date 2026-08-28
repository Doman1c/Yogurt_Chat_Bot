from enum import Enum
from typing import Generic, TypeVar

from ..foundation.identifiers import CapabilityId
from ..foundation.model import FrozenContract
from ..foundation.versioning import ContractVersion


class CapabilityState(str, Enum): AVAILABLE = "available"; DEGRADED = "degraded"; UNAVAILABLE = "unavailable"; INCOMPATIBLE = "incompatible"


class BaseCapabilityDescriptor(FrozenContract):
    """Base for typed extensions; central framework has no descriptor union."""


DescriptorT = TypeVar("DescriptorT", bound=BaseCapabilityDescriptor)


class ContractCompatibilityDecision(str, Enum):
    COMPATIBLE = "compatible"
    MAJOR_MISMATCH = "major_mismatch"
    MINOR_TOO_OLD = "minor_too_old"


def contract_compatibility_decision(
    required: ContractVersion, offered: ContractVersion
) -> ContractCompatibilityDecision:
    if required.major != offered.major:
        return ContractCompatibilityDecision.MAJOR_MISMATCH
    if offered.minor < required.minor:
        return ContractCompatibilityDecision.MINOR_TOO_OLD
    return ContractCompatibilityDecision.COMPATIBLE


def is_contract_compatible(required: ContractVersion, offered: ContractVersion) -> bool:
    return contract_compatibility_decision(required, offered) == ContractCompatibilityDecision.COMPATIBLE


class CapabilityRequirement(FrozenContract):
    capability_id: CapabilityId
    minimum_contract_version: ContractVersion
    required: bool = True
