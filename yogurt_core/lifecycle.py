"""Core-local lifecycle gate for R2-C startup, recovery, and shutdown."""

from enum import Enum


class CoreLifecycleState(str, Enum):
    STARTING = "starting"
    RECOVERING = "recovering"
    SERVING = "serving"
    DRAINING = "draining"
    STOPPED = "stopped"
    NOT_SERVING = "not_serving"
