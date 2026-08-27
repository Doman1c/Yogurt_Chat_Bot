from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MemoryServiceClient:
    """Small fail-open client for Native runtimes and integration tests."""

    base_url: str = "http://127.0.0.1:17621"
    timeout_seconds: float = 0.35

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path not in {"", "/"}:
            raise ValueError("MemoryServiceClient only permits http://127.0.0.1:<port>")

    def recall(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._post("/v1/memory/recall", payload)

    def observe(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._post("/v1/memory/observe", payload)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url.rstrip('/')}{path}", body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urlopen(request, timeout=max(0.05, self.timeout_seconds)) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
