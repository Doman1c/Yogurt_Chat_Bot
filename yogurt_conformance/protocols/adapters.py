"""Test-only behavior protocols consumed by reusable R1 conformance profiles."""

from typing import Protocol


class ScenarioAdapterProtocol(Protocol):
    def run_scenario(self, name: str) -> bool:
        ...


class CorePublicAdapterProtocol(ScenarioAdapterProtocol, Protocol): pass
class MemoryAdapterProtocol(ScenarioAdapterProtocol, Protocol): pass
class SurfaceAdapterProtocol(ScenarioAdapterProtocol, Protocol): pass
class VoiceAdapterProtocol(ScenarioAdapterProtocol, Protocol): pass
class OperationProviderAdapterProtocol(ScenarioAdapterProtocol, Protocol): pass
class CapabilityProviderAdapterProtocol(ScenarioAdapterProtocol, Protocol): pass
class RuntimeLifecycleAdapterProtocol(ScenarioAdapterProtocol, Protocol): pass
