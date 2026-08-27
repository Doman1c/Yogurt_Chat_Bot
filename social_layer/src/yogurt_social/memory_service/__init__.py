"""Shared, local-only Yogurt Memory V5 service.

The package deliberately keeps FastAPI at the edge.  Importing the core client
or service does not require a web server dependency, which keeps old OpenClaw
paths available during rollout and makes failures easy to contain.
"""

from .core import MemoryService, MemoryServiceConfig

__all__ = ["MemoryService", "MemoryServiceConfig"]
