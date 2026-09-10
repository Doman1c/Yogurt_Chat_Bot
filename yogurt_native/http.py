"""HTTP is confined to this adapter; authentication precedes body parsing."""
import time
import re
from contextlib import asynccontextmanager
from typing import AsyncIterator, Tuple, cast
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from yogurt_contracts.surface import NativeSurfaceTurnRequest, SurfaceConversationRef, SurfaceConversationHistory, SurfaceError, SurfaceTurnAccepted
from yogurt_core.ports.state_store import CoreStateError
from .gateway import NativeGateway, SurfaceFailure
from .identity import OwnerProfile, ServerIdentityMapping

MAX_BODY_BYTES = 100000

class AuthBoundary:
    def __init__(self, app: ASGIApp, identities: ServerIdentityMapping) -> None:
        self.app, self.identities = app, identities

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received_at = time.monotonic()
        headers = scope.get("headers", [])
        tokens = [value for key, value in headers if key.lower() == b"authorization"]
        profile = self.identities.authenticate(tokens[0].decode("latin1")) if len(tokens) == 1 else None
        if profile is None:
            await JSONResponse({"code": "UNAUTHORIZED"}, status_code=401,
                               headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        scope.setdefault("state", {})["profile"] = profile
        scope["state"]["received_at"] = received_at
        # Buffer only a bounded request before routing. Do not inspect/log its contents.
        if scope["method"] == "POST":
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_BODY_BYTES:
                    await JSONResponse({"code": "INPUT_TOO_LARGE"}, status_code=413)(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            async def buffered_receive() -> dict[str, object]:
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            # Receive's TypedDict carries optional ASGI keys.
            await self.app(scope, cast(Receive, buffered_receive), send)
        else:
            await self.app(scope, receive, send)


def create_app(gateway: NativeGateway, identities: ServerIdentityMapping) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await gateway.core.start()
        try:
            yield
        finally:
            await gateway.core.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(AuthBoundary, identities=identities)

    @app.exception_handler(SurfaceFailure)
    async def surface_error(request: Request, error: SurfaceFailure) -> JSONResponse:
        return JSONResponse(SurfaceError(code=error.code).model_dump(), status_code=error.status)

    @app.exception_handler(CoreStateError)
    async def core_error(request: Request, error: CoreStateError) -> JSONResponse:
        missing = error.code in {"READ_NOT_AUTHORIZED", "CONVERSATION_NOT_FOUND", "RESPONSE_NOT_FOUND", "TURN_NOT_FOUND"}
        return JSONResponse({"code": "NOT_FOUND" if missing else "CORE_UNAVAILABLE"}, status_code=404 if missing else 503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"code": "INVALID_REQUEST"}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"code": "CORE_UNAVAILABLE"}, status_code=503)

    def profile(request: Request) -> OwnerProfile:
        return cast(OwnerProfile, request.state.profile)

    @app.post("/v1/native/turns", status_code=202)
    async def submit(request: Request, body: NativeSurfaceTurnRequest) -> SurfaceTurnAccepted:
        return await gateway.submit(profile(request), body, request.state.received_at)

    @app.get("/v1/native/responses/{response_id}/events")
    async def events(request: Request, response_id: str) -> StreamingResponse:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", response_id):
            raise SurfaceFailure("INVALID_RESPONSE_ID", 422)
        cursor = request.headers.get("last-event-id", "0")
        if not re.fullmatch(r"[0-9]{1,10}", cursor):
            raise SurfaceFailure("INVALID_EVENT_CURSOR", 422)
        await gateway.authorize_response(profile(request), response_id)
        return StreamingResponse(gateway.events(profile(request), response_id, int(cursor)),
            media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/v1/native/conversations")
    async def conversations(request: Request) -> Tuple[SurfaceConversationRef, ...]:
        return await gateway.conversations(profile(request))

    @app.get("/v1/native/conversations/{conversation_id}")
    async def history(request: Request, conversation_id: str) -> SurfaceConversationHistory:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", conversation_id):
            raise SurfaceFailure("INVALID_CONVERSATION_ID", 422)
        return await gateway.history(profile(request), conversation_id)

    @app.get("/v1/native/health")
    async def health(request: Request) -> dict[str, object]:
        await gateway.conversations(profile(request))
        return {"status": "ok", "model_token_streaming": gateway.token_streaming}

    return app
