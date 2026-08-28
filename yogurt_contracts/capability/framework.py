from enum import Enum
from typing import Generic, TypeVar

from ..foundation.identifiers import CapabilityId
from ..foundation.model import FrozenContract


class CapabilityState(str, Enum): AVAILABLE = "available"; DEGRADED = "degraded"; UNAVAILABLE = "unavailable"; INCOMPATIBLE = "incompatible"


class BaseCapabilityDescriptor(FrozenContract):
    """Base for typed extensions; central framework has no descriptor union."""


DescriptorT = TypeVar("DescriptorT", bound=BaseCapabilityDescriptor)


class CapabilityRequirement(FrozenContract):
    capability_id: CapabilityId
    required: bool = True
