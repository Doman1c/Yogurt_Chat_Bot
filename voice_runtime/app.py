from __future__ import annotations

import asyncio
import argparse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from .config import ConfigError, RuntimeConfig, load_config
from .engines import YogurtTtsAdapter, YogurtTtsError
from .schemas import HealthResponse, SpeechRequest


def create_app(config: RuntimeConfig) -> FastAPI:
    app = FastAPI(
        title="Yogurt Voice Runtime",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    adapter = YogurtTtsAdapter(config.engine)
    semaphore = asyncio.Semaphore(config.limits.max_concurrent_requests)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        reachable = await adapter.reachable()
        return HealthResponse(
            status="ok" if reachable else "degraded",
            engine_reachable=reachable,
            default_profile=config.default_profile,
            profiles=sorted(config.profiles.keys()) or ["yogurt"],
        )

    @app.post("/v1/speech")
    async def speech(request: SpeechRequest) -> Response:
        if len(request.text) > config.limits.max_text_chars:
            raise HTTPException(status_code=422, detail="text_too_long")

        canonical_emotion = {"angry": "upset", "surprised": "excited"}.get(request.emotion, request.emotion)

        try:
            async with semaphore:
                result = await asyncio.wait_for(
                    adapter.synthesize(
                        text=request.text,
                        language=request.language,
                        emotion=canonical_emotion,
                        intensity=request.intensity,
                    ),
                    timeout=config.limits.max_request_seconds,
                )
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="tts_timeout") from exc
        except YogurtTtsError as exc:
            # Intentionally do not relay local upstream response bodies or paths.
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return Response(
            content=result.audio,
            media_type=result.media_type,
            headers={
                "X-Yogurt-Voice": request.voice,
                "X-Yogurt-Language": result.language,
                "X-Yogurt-Emotion-Requested": request.emotion,
                "X-Yogurt-Emotion-Canonical": canonical_emotion,
                "X-Yogurt-Emotion-Used": result.used_emotion,
                "X-Yogurt-Intensity": "omitted" if request.intensity is None else f"{request.intensity:.3f}",
            },
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Yogurt local Voice Runtime")
    parser.add_argument(
        "--config",
        default=None,
        help="local TOML config path (defaults to voice_runtime/config.toml)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"Voice Runtime config error: {exc}") from exc

    import uvicorn

    uvicorn.run(
        create_app(config),
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
