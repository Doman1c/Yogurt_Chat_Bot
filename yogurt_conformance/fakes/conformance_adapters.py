"""Intentionally small good/bad adapters used to prove profiles detect behavior violations."""

from typing import FrozenSet


class GoodScenarioAdapter:
    def run_scenario(self, name: str) -> bool:
        return True


class BadScenarioAdapter:
    def __init__(self, failed_scenarios: FrozenSet[str]) -> None:
        self._failed_scenarios = failed_scenarios

    def run_scenario(self, name: str) -> bool:
        return name not in self._failed_scenarios


class CommitMeansSeenMemoryFake(BadScenarioAdapter):
    def __init__(self) -> None: super().__init__(frozenset(("commit_is_not_exposure",)))
class ForgedReadReceiptSurface(BadScenarioAdapter):
    def __init__(self) -> None: super().__init__(frozenset(("presented_not_read",)))
class DraftSpeakingVoiceFake(BadScenarioAdapter):
    def __init__(self) -> None: super().__init__(frozenset(("reject_draft",)))
class DispatchWithoutIntentFake(BadScenarioAdapter):
    def __init__(self) -> None: super().__init__(frozenset(("intent_before_dispatch",)))
class DuplicateTurnExecutionFake(BadScenarioAdapter):
    def __init__(self) -> None: super().__init__(frozenset(("turn_retry_idempotent",)))
