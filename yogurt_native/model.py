"""Local Ollama final-text adapter. No fabricated token deltas."""
import asyncio
import json
import urllib.request
from typing import AsyncIterator
from yogurt_contracts.execution import ExecutionAttemptSnapshot
from yogurt_core.model.events import ModelDraftEvent, VisibleTextDelta
from yogurt_core.model.execution import ModelContext

class LocalOllamaModel:
    def __init__(self, model: str) -> None:
        if not model:
            raise ValueError("configure the existing Ollama model name")
        self.model = model

    def _generate(self, context: ModelContext) -> str:
        messages = [{"role": "system", "content": context.persona_material}]
        messages.extend({"role": entry.role.value, "content": entry.text} for entry in context.history)
        messages.append({"role": "user", "content": context.user_input})
        body = json.dumps({"model": self.model, "messages": messages, "stream": False}).encode()
        request = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body,
                                         headers={"Content-Type": "application/json"})
        # Disable environment proxies for private loopback model traffic.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=120) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("model output exceeded bound")
        value = json.loads(raw)
        text = value["message"]["content"]
        if not isinstance(text, str):
            raise ValueError("invalid model response")
        return text

    async def run(self, context: ModelContext, attempt: ExecutionAttemptSnapshot) -> AsyncIterator[ModelDraftEvent]:
        text = await asyncio.to_thread(self._generate, context)
        if text:
            yield VisibleTextDelta(text=text)

    async def cancel(self, attempt: ExecutionAttemptSnapshot) -> None:
        # The local non-streaming HTTP call cannot be interrupted. Core fencing
        # prevents late output from being committed after explicit cancellation.
        return None
