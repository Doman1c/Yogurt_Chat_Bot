from typing import AsyncIterator, List, Sequence, Set

from yogurt_core.model.events import ModelDraftEvent
from yogurt_core.model.execution import ExecutionAttemptSnapshot, ModelContext


class FakeModelAdapter:
    """Deterministic provider-neutral test adapter; may intentionally ignore cancel."""

    def __init__(self, attempts: Sequence[Sequence[ModelDraftEvent]], ignore_cancellation: bool = False) -> None:
        self._attempts = list(attempts)
        self._calls = 0
        self._cancelled: Set[str] = set()
        self._ignore_cancellation = ignore_cancellation
        self.contexts: List[ModelContext] = []

    async def run(self, context: ModelContext, attempt: ExecutionAttemptSnapshot) -> AsyncIterator[ModelDraftEvent]:
        self.contexts.append(context)
        index = self._calls
        self._calls += 1
        events = self._attempts[index] if index < len(self._attempts) else ()
        for event in events:
            if attempt.execution_attempt_id.value in self._cancelled and not self._ignore_cancellation:
                return
            yield event

    async def cancel(self, attempt: ExecutionAttemptSnapshot) -> None:
        self._cancelled.add(attempt.execution_attempt_id.value)
