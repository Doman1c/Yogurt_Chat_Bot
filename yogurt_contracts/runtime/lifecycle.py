from enum import Enum


class ProcessState(str, Enum): STARTING = "starting"; RUNNING = "running"; STOPPING = "stopping"; EXITED = "exited"
class ServiceState(str, Enum): NOT_READY = "not_ready"; SERVING = "serving"; DEGRADED = "degraded"
class CapabilityState(str, Enum): AVAILABLE = "available"; DEGRADED = "degraded"; UNAVAILABLE = "unavailable"; INCOMPATIBLE = "incompatible"
