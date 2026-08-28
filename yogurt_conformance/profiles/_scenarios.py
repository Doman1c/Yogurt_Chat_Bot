from typing import Iterable

from yogurt_conformance.protocols.adapters import ScenarioAdapterProtocol


def assert_scenarios(adapter: ScenarioAdapterProtocol, scenarios: Iterable[str]) -> None:
    for scenario in scenarios:
        assert adapter.run_scenario(scenario), "adapter failed R1 conformance scenario: " + scenario
