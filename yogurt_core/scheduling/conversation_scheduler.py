import asyncio
from typing import Coroutine, Dict, Any


class ConversationScheduler:
    """Best-effort task registry. Store claims, never this registry, establish correctness."""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task[None]] = {}
        self._failures: list[BaseException] = []

    def schedule(self, key: str, work: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(work)
        self._tasks[key] = task
        task.add_done_callback(lambda finished: self._record_completion(key, finished))

    async def drain(self) -> None:
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks)
        if self._failures:
            raise self._failures.pop(0)

    async def cancel_remaining(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _record_completion(self, key: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._failures.append(error)
