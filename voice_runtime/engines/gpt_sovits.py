from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

from ..config import EngineConfig, VoiceProfile


class GptSovitsError(RuntimeError):
    pass


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    media_type: str
    speed: float


class GptSovitsAdapter:
    """Loopback-only adapter for GPT-SoVITS official api.py."""

    def __init__(self, engine: EngineConfig) -> None:
        self.engine = engine

    @staticmethod
    def build_payload(
        *,
        text: str,
        language: str,
        profile: VoiceProfile,
        speed: float | None,
    ) -> tuple[dict[str, object], float]:
        selected_speed = speed
        if selected_speed is None:
            selected_speed = profile.zh_speed if language == "zh" else profile.ja_speed

        payload: dict[str, object] = {
            "refer_wav_path": profile.reference_path,
            "prompt_text": profile.reference_text,
            "prompt_language": profile.prompt_language,
            "text": text,
            "text_language": language,
            "top_k": profile.top_k,
            "top_p": profile.top_p,
            "temperature": profile.temperature,
            "speed": selected_speed,
        }
        if profile.extra_reference_paths:
            payload["inp_refs"] = list(profile.extra_reference_paths)
        return payload, float(selected_speed)

    async def reachable(self) -> bool:
        parsed = urlparse(self.engine.base_url)
        host = parsed.hostname
        if host is None:
            return False
        port = parsed.port or 80
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            del reader
            return True
        except Exception:
            return False

    async def synthesize(
        self,
        *,
        text: str,
        language: str,
        profile: VoiceProfile,
        speed: float | None,
    ) -> SynthesisResult:
        # Import lazily so config/payload tests do not need network dependencies.
        import httpx

        payload, selected_speed = self.build_payload(
            text=text,
            language=language,
            profile=profile,
            speed=speed,
        )

        try:
            async with httpx.AsyncClient(
                timeout=self.engine.timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.post(self.engine.base_url, json=payload)
        except httpx.HTTPError as exc:
            raise GptSovitsError("gpt_sovits_unreachable") from exc

        if response.status_code != 200:
            # Do not relay upstream bodies: they may contain local private paths.
            raise GptSovitsError(f"gpt_sovits_http_{response.status_code}")

        media_type = response.headers.get("content-type", "audio/wav").split(";", 1)[0]
        if not media_type.startswith("audio/"):
            raise GptSovitsError("gpt_sovits_invalid_media_type")
        if not response.content:
            raise GptSovitsError("gpt_sovits_empty_audio")

        return SynthesisResult(
            audio=response.content,
            media_type=media_type,
            speed=selected_speed,
        )
