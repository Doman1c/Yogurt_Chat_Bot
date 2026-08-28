from enum import IntEnum


class DurabilityLevel(IntEnum):
    EPHEMERAL_COMMITTED = 0
    JOURNALED = 1
    DURABLY_PERSISTED = 2


def durability_satisfies(achieved: DurabilityLevel, required: DurabilityLevel) -> bool:
    """Semantic commit and storage durability are intentionally separate axes."""
    return achieved >= required
