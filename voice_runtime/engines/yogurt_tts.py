"""Public gateway adapter for the local Yogurt Daily TTS service (port 17861)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..config import EngineConfig


class YogurtTtsError(RuntimeError):
    pass


@dataclass(frozen=True)
class YogurtTtsResult:
    audio: bytes
    media_type: str
    requested_emotion: str
    used_emotion: str
    language: str
    intensity: float | None


class YogurtTtsAdapter:
    """Translate the public gateway contract without exposing reference assets."""

    def __init__(self, engine: EngineConfig) -> None:
        self.engine = engine

    @staticmethod
    def build_payload(*, text: str, language: str, emotion: str, intensity: float | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text, "language": language, "emotion": emotion}
        # Omission means the service uses its configured baseline. It is not
        # equivalent to inventing a numeric default at the gateway.
        if intensity is not None:
            payload["intensity"] = intensity
        return payload

    async def reachable(self) -> bool:
        parsed = urlparse(self.engine.base_url)
        if not parsed.hostname:
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, parsed.port or 80), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            del reader
            return True
        except Exception:
            return False

    async def synthesize(self, *, text: str, language: str, emotion: str,
                         intensity: float | None) -> YogurtTtsResult:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.engine.timeout_seconds, trust_env=False) as client:
                response = await client.post(f"{self.engine.base_url}/v1/tts", json=self.build_payload(
                    text=text, language=language, emotion=emotion, intensity=intensity
                ))
        except httpx.HTTPError as exc:
            raise YogurtTtsError("yogurt_tts_unreachable") from exc
        if response.status_code != 200:
            raise YogurtTtsError(f"yogurt_tts_http_{response.status_code}")
        media_type = response.headers.get("content-type", "audio/wav").split(";", 1)[0]
        if not media_type.startswith("audio/") or not response.content:
            raise YogurtTtsError("yogurt_tts_invalid_audio")
        return YogurtTtsResult(
            audio=response.content, media_type=media_type,
            requested_emotion=emotion,
            used_emotion=response.headers.get("X-Yogurt-Emotion-Used", "neutral"),
            language=response.headers.get("X-Yogurt-Language", language), intensity=intensity,
        )
