from enum import Enum

from ..capability.framework import CapabilityState


class ProcessState(str, Enum): STARTING = "starting"; RUNNING = "running"; STOPPING = "stopping"; EXITED = "exited"
class ServiceState(str, Enum): NOT_READY = "not_ready"; SERVING = "serving"; DEGRADED = "degraded"
