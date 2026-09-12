"""Real loopback Ollama NDJSON streaming with bounded deterministic coalescing."""
import asyncio
import json
from typing import AsyncGenerator, AsyncIterator, Dict, Optional

import httpx

from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_core.model.events import AttemptTerminal, AttemptTerminalKind, ModelDraftEvent, VisibleTextDelta
from yogurt_core.model.execution import ModelContext

MAX_LINE_BYTES = 65536
MAX_STREAM_BYTES = 8_000_000
MAX_TEXT_CHARS = 2_000_000
CHUNK_CHARS = 80
MIN_BOUNDARY_CHARS = 40


class LocalOllamaModel:
    def __init__(self, model: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        if not model:
            raise ValueError("configure the existing Ollama model name")
        self.model = model
        self._transport = transport
        self._cancellations: Dict[str, asyncio.Event] = {}

    async def _lines(self, response: httpx.Response) -> AsyncIterator[bytes]:
        pending = bytearray()
        total = 0
        async for block in response.aiter_bytes():
            total += len(block)
            if total > MAX_STREAM_BYTES:
                raise ValueError("stream exceeded bound")
            # Split transport buffers without treating their boundaries as records.
            start = 0
            while start < len(block):
                end = block.find(b"\n", start)
                stop = len(block) if end < 0 else end
                if len(pending) + stop - start > MAX_LINE_BYTES:
                    raise ValueError("stream object exceeded bound")
                pending.extend(block[start:stop])
                if end < 0:
                    break
                yield bytes(pending)
                pending.clear()
                start = end + 1
        if pending:
            yield bytes(pending)  # A terminal object need not end in newline.

    async def _stream(self, context: ModelContext) -> AsyncGenerator[str, None]:
        messages = [{"role": "system", "content": context.persona_material}]
        messages.extend({"role": entry.role.value, "content": entry.text} for entry in context.history)
        if context.working_state is not None:
            messages.append({"role": "user", "content": context.working_state.material})
        messages.append({"role": "user", "content": context.user_input})
        body = {"model": self.model, "messages": messages, "stream": True}
        # No proxies, redirects or client-selected endpoints on the private path.
        async with httpx.AsyncClient(trust_env=False, timeout=120, transport=self._transport) as client:
            async with client.stream("POST", "http://127.0.0.1:11434/api/chat", json=body) as response:
                response.raise_for_status()
                pending = ""
                total = 0
                async for line in self._lines(response):
                    value = json.loads(line.decode("utf-8"))
                    if not isinstance(value, dict) or "error" in value or type(value.get("done")) is not bool:
                        raise ValueError("invalid provider object")
                    message = value.get("message")
                    if message is None and value["done"] and "message" not in value:
                        text = ""
                    elif isinstance(message, dict) and isinstance(message.get("content"), str):
                        text = message["content"]
                    else:
                        raise ValueError("invalid provider content")
                    # Reject unpaired JSON surrogates before committing invalid UTF-8.
                    text.encode("utf-8")
                    total += len(text)
                    if total > MAX_TEXT_CHARS:
                        raise ValueError("model output exceeded bound")
                    # Only coalesce real provider text. No timing or synthetic output.
                    for character in text:
                        pending += character
                        if (len(pending) >= CHUNK_CHARS or character == "\n" or
                            (len(pending) >= MIN_BOUNDARY_CHARS and character in ".!?;。！？；")):
                            yield pending
                            pending = ""
                    if value["done"]:
                        if pending:
                            yield pending
                        return
                raise ValueError("provider EOF before done")

    async def run(self, context: ModelContext, attempt: ExecutionAttemptSnapshot) -> AsyncIterator[ModelDraftEvent]:
        key = attempt.execution_attempt_id.value
        cancelled = asyncio.Event()
        self._cancellations[key] = cancelled
        stream = self._stream(context)
        stop = asyncio.create_task(cancelled.wait())
        pending: Optional[asyncio.Task[Optional[str]]] = None

        async def next_chunk() -> Optional[str]:
            try:
                return await stream.__anext__()
            except StopAsyncIteration:
                return None

        try:
            while not cancelled.is_set():
                pending = asyncio.create_task(next_chunk())
                await asyncio.wait((pending, stop), return_when=asyncio.FIRST_COMPLETED)
                if cancelled.is_set():
                    break
                text = await pending
                if text is None:
                    return
                yield VisibleTextDelta(text=text)
            yield AttemptTerminal(kind=AttemptTerminalKind.CANCELLED)
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
            # Typed failure feeds existing Core prefix-aware retry/abort semantics.
            yield AttemptTerminal(kind=AttemptTerminalKind.FAILED, reason="OLLAMA_STREAM_FAILURE")
        finally:
            stop.cancel()
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await asyncio.gather(stop, return_exceptions=True)
            await stream.aclose()
            self._cancellations.pop(key, None)

    async def cancel(self, attempt: ExecutionAttemptSnapshot) -> None:
        cancelled = self._cancellations.get(attempt.execution_attempt_id.value)
        if cancelled is not None:
            cancelled.set()
