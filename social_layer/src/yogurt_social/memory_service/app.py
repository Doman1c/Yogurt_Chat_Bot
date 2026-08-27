from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from .core import MemoryService, MemoryServiceConfig
from .schemas import ObserveRequest, RecallRequest


def _default_db_path() -> Path:
    return Path(os.environ.get("YOGURT_SOCIAL_DB", "~/.openclaw/yogurt-social/memory.sqlite3")).expanduser()


def create_app(service: Optional[MemoryService] = None) -> FastAPI:
    memory_service = service or MemoryService(MemoryServiceConfig(db_path=_default_db_path()))
    app = FastAPI(title="Yogurt Memory Service", version="5.0.0")
    app.state.memory_service = memory_service

    @app.get("/v1/health")
    def health() -> dict[str, object]:
        return {"ok": True, "service": "yogurt-memory-v5"}

    @app.get("/v1/ready")
    def ready() -> dict[str, object]:
        return memory_service.readiness()

    @app.post("/v1/memory/recall")
    def recall(request: RecallRequest) -> dict[str, object]:
        try:
            payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
            return memory_service.recall(**payload)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid_memory_request") from exc

    @app.post("/v1/memory/observe")
    def observe(request: ObserveRequest) -> dict[str, object]:
        try:
            payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
            return memory_service.observe(**payload)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid_memory_request") from exc

    @app.get("/v1/memory/snapshot")
    def snapshot(persona_id: str = "yogurt", user_id: str = "owner") -> dict[str, object]:
        return memory_service.snapshot(persona_id=persona_id, user_id=user_id)

    @app.get("/v1/memory/candidates")
    def candidates(persona_id: str = "yogurt", user_id: str = "owner", status: Optional[str] = None) -> list[dict[str, object]]:
        return memory_service.candidates(persona_id=persona_id, user_id=user_id, status=status)

    @app.get("/v1/memory/revisions")
    def revisions(persona_id: str = "yogurt", user_id: str = "owner", limit: int = Query(default=50, ge=1, le=500)) -> list[dict[str, object]]:
        return memory_service.revisions(persona_id=persona_id, user_id=user_id, limit=limit)

    return app


def create_default_app() -> FastAPI:
    """Uvicorn factory that defers database creation until service startup."""
    return create_app()
