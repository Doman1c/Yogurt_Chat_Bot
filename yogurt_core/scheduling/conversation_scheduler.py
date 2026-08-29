import asyncio
from typing import Coroutine, Dict, Any


class ConversationScheduler:
    """Best-effort task registry. Store claims, never this registry, establish correctness."""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task[None]] = {}

    def schedule(self, key: str, work: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(work)
        self._tasks[key] = task
        task.add_done_callback(lambda _: self._tasks.pop(key, None))

    async def drain(self) -> None:
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
