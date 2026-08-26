"""Resident, reference-routed GPT-SoVITS wrapper with no private default paths."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from contextlib import asynccontextmanager, contextmanager
from io import BytesIO
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from emotion_registry import EmotionRegistry


class Request(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    language: str = "auto"
    emotion: str = "neutral"
    intensity: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("language")
    @classmethod
    def valid_language(cls, value: str) -> str:
        if value not in {"auto", "zh", "ja", "en"}:
            raise ValueError("language must be auto, zh, ja, or en")
        return value


@contextmanager
def temporary_upstream_cwd(path: Path):
    """Upstream uses relative assets; always restore CWD after its call."""
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class Engine:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pipeline = None
        self.loaded = False
        self.home = Path(os.environ["YOGURT_GPT_SOVITS_HOME"]).resolve()
        self.gpt = Path(os.environ["YOGURT_TTS_GPT_MODEL"]).resolve()
        self.sovits = Path(os.environ["YOGURT_TTS_SOVITS_MODEL"]).resolve()
        self.registry = EmotionRegistry(Path(os.environ["YOGURT_TTS_EMOTION_REGISTRY"]).resolve())
        self.temporary: tempfile.TemporaryDirectory[str] | None = None

    def load(self) -> None:
        for item in (self.home, self.gpt, self.sovits):
            if not item.exists():
                raise FileNotFoundError(item)
        if not torch.cuda.is_available():
            raise RuntimeError("Yogurt Daily TTS requires CUDA")
        sys.path.insert(0, str(self.home))
        config = {"custom": {
            "device": "cuda", "is_half": True, "version": os.getenv("YOGURT_TTS_MODEL_VERSION", "v2Pro"),
            "t2s_weights_path": str(self.gpt), "vits_weights_path": str(self.sovits),
            "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
            "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
        }}
        self.temporary = tempfile.TemporaryDirectory(prefix="yogurt_tts_")
        local_config = Path(self.temporary.name) / "tts_infer.yaml"
        local_config.write_text(yaml.safe_dump(config), encoding="utf-8")
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
        with temporary_upstream_cwd(self.home):
            self.pipeline = TTS(TTS_Config(str(local_config)))
        self.loaded = True

    def synthesize(self, request: Request) -> bytes:
        if self.pipeline is None:
            raise RuntimeError("engine not loaded")
        language = detect_language(request.text) if request.language == "auto" else request.language
        if language not in {"auto", "zh", "ja", "en"}:
            raise ValueError("language must be auto, zh, ja, or en")
        route = self.registry.route(request.emotion)
        payload = {
            "text": request.text, "text_lang": language,
            "ref_audio_path": str(route.audio), "aux_ref_audio_paths": [],
            "prompt_text": route.prompt_text, "prompt_lang": route.prompt_language,
            "top_k": 15, "top_p": 1.0, "temperature": 1.0,
            "text_split_method": "cut5", "batch_size": 1, "batch_threshold": 0.75,
            "split_bucket": True, "speed_factor": 1.0, "fragment_interval": 0.3,
            "seed": -1, "parallel_infer": True, "repetition_penalty": 1.35,
            "sample_steps": 32, "super_sampling": False, "return_fragment": False,
            "streaming_mode": False, "overlap_length": 2, "min_chunk_length": 16,
            "fixed_length_chunk": False,
        }
        with self.lock, temporary_upstream_cwd(self.home):
            sample_rate, audio = next(self.pipeline.run(payload))
        samples = np.asarray(audio)
        if np.issubdtype(samples.dtype, np.integer):
            samples = samples.astype(np.float32) / np.iinfo(samples.dtype).max
        buffer = BytesIO()
        sf.write(buffer, samples.astype(np.float32), sample_rate, format="WAV", subtype="PCM_16")
        return buffer.getvalue(), route

    def close(self) -> None:
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None


def detect_language(text: str) -> str:
    has_han = any("\u4e00" <= char <= "\u9fff" for char in text)
    has_kana = any("\u3040" <= char <= "\u30ff" for char in text)
    has_latin = any(char.isascii() and char.isalpha() for char in text)
    if sum((has_han, has_kana, has_latin)) > 1:
        return "auto"
    return "ja" if has_kana else "zh" if has_han else "en"


engine: Engine | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global engine
    engine = Engine()
    engine.load()
    engine.synthesize(Request(text="おかえり。", language="ja"))
    yield
    engine.close()


app = FastAPI(title="Yogurt Daily TTS", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict:
    return {"service": "yogurt-daily-tts", "status": "ready" if engine and engine.loaded else "loading", "docs": "/docs"}


@app.get("/v1/health")
async def health() -> dict:
    return {"status": "ok", "service": "yogurt-daily-tts"}


@app.get("/v1/ready")
async def ready() -> dict:
    if engine is None or not engine.loaded:
        raise HTTPException(status_code=503, detail="model is loading")
    return {"status": "ready", "model_loaded": True, "cuda": torch.cuda.is_available()}


@app.get("/v1/emotions")
async def emotions() -> dict:
    return {"emotions": engine.registry.public_profiles() if engine else []}


@app.post("/v1/tts/debug")
async def tts_debug(request: Request) -> dict:
    if engine is None:
        raise HTTPException(status_code=503, detail="model is loading")
    route = engine.registry.route(request.emotion)
    return {"requested_emotion": route.requested, "canonical_emotion": route.canonical,
            "used_emotion": route.used, "intensity": request.intensity, "reference_id": route.reference_id,
            "role": route.role, "selection": route.selection, "fallback": route.fallback,
            "language": detect_language(request.text) if request.language == "auto" else request.language,
            "model_loaded": engine.loaded, "cuda": torch.cuda.is_available()}


@app.post("/v1/tts")
async def tts(request: Request) -> Response:
    if engine is None or not engine.loaded:
        raise HTTPException(status_code=503, detail="model is loading")
    try:
        audio, route = await asyncio.to_thread(engine.synthesize, request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="TTS generation failed") from exc
    return Response(audio, media_type="audio/wav", headers={"X-Yogurt-Emotion-Requested": route.requested,
        "X-Yogurt-Emotion-Used": route.used, "X-Yogurt-Fallback": str(route.fallback).lower(),
        "X-Yogurt-Language": detect_language(request.text) if request.language == "auto" else request.language})


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("YOGURT_TTS_HOST", "127.0.0.1"), port=int(os.getenv("YOGURT_TTS_PORT", "17861")))
