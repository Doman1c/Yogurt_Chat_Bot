from enum import Enum


class ContractVisibility(str, Enum):
    """Architecture ownership metadata; it is not a runtime access check."""

    PUBLIC_CROSS_RUNTIME = "public_cross_runtime"
    CORE_DOMAIN_INTERNAL = "core_domain_internal"
    LOCAL_IMPLEMENTATION = "local_implementation"
