from enum import Enum, IntEnum


class DurabilityLevel(IntEnum):
    EPHEMERAL_COMMITTED = 0
    JOURNALED = 1
    DURABLY_PERSISTED = 2


class CorePersistenceMode(str, Enum):
    """Whether this core instance may authoritatively dispatch external side effects."""

    EPHEMERAL = "ephemeral"
    DURABLE = "durable"


def durability_satisfies(achieved: DurabilityLevel, required: DurabilityLevel) -> bool:
    """Semantic commit and storage durability are intentionally separate axes."""
    return achieved >= required
