from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from voice_runtime.schemas import SpeechRequest


class VoiceRuntimeError(RuntimeError):
    pass


def validate_runtime_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http":
        raise VoiceRuntimeError("voice_runtime_url_must_use_http")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VoiceRuntimeError("voice_runtime_url_must_not_contain_credentials_query_fragment")
    if not parsed.hostname:
        raise VoiceRuntimeError("voice_runtime_url_missing_host")
    host = parsed.hostname.strip().lower()
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise VoiceRuntimeError("voice_runtime_url_must_be_loopback")
        except ValueError as exc:
            raise VoiceRuntimeError("voice_runtime_url_must_be_loopback") from exc
    return value.rstrip("/")


@dataclass(frozen=True)
class SynthesizedAudio:
    audio: bytes
    media_type: str
    voice: str
    language: str
    speed: str | None


class VoiceRuntimeClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8770",
        timeout_seconds: float = 180.0,
    ) -> None:
        self.base_url = validate_runtime_url(base_url)
        if not (1 <= timeout_seconds <= 600):
            raise VoiceRuntimeError("voice_runtime_timeout_out_of_range")
        self.timeout_seconds = timeout_seconds

    async def reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=min(self.timeout_seconds, 5.0),
                trust_env=False,
            ) as client:
                response = await client.get("/health")
            if response.status_code != 200:
                return False
            payload = response.json()
            return bool(payload.get("engine_reachable")) and payload.get("status") == "ok"
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def synthesize(self, request: SpeechRequest) -> SynthesizedAudio:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(
                    "/v1/speech",
                    json=request.model_dump(mode="json"),
                )
        except httpx.TimeoutException as exc:
            raise VoiceRuntimeError("voice_runtime_timeout") from exc
        except httpx.HTTPError as exc:
            raise VoiceRuntimeError("voice_runtime_unreachable") from exc

        if response.status_code != 200:
            # Do not relay response bodies because downstream errors may contain local details.
            raise VoiceRuntimeError(f"voice_runtime_http_{response.status_code}")

        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if not media_type.startswith("audio/"):
            raise VoiceRuntimeError("voice_runtime_invalid_content_type")
        if not response.content:
            raise VoiceRuntimeError("voice_runtime_empty_audio")

        return SynthesizedAudio(
            audio=response.content,
            media_type=media_type,
            voice=response.headers.get("x-yogurt-voice", request.voice),
            language=response.headers.get("x-yogurt-language", request.language),
            speed=response.headers.get("x-yogurt-speed"),
        )
