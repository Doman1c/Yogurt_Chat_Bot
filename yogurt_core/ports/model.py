from typing import AsyncIterator, Protocol

from yogurt_contracts.execution import ExecutionAttemptSnapshot

from yogurt_core.model.events import ModelDraftEvent
from yogurt_core.model.execution import ModelContext


class ModelPort(Protocol):
    """A provider-neutral model boundary; no Ollama/Qwen protocol crosses it."""

    def run(self, context: ModelContext, attempt: ExecutionAttemptSnapshot) -> AsyncIterator[ModelDraftEvent]:
        ...

    async def cancel(self, attempt: ExecutionAttemptSnapshot) -> None:
        ...
