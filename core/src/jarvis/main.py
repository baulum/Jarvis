"""FastAPI application factory and server entry point."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from jarvis.api.exposure import PublicExposureGuard
from jarvis.api.realtime import router as realtime_router
from jarvis.api.routes import router as api_router
from jarvis.api.voice_ws import router as voice_router
from jarvis.app_state import VERSION, AppState
from jarvis.config import Settings, get_settings
from jarvis.db import dispose_engine
from jarvis.db.migrate import upgrade_to_head
from jarvis.devices.api import router as devices_router
from jarvis.devices.protocol import MAX_FRAME_BYTES
from jarvis.logging_setup import setup_logging
from jarvis.mcp.server import MCPAuthApp, build_mcp
from jarvis.mobile.api import router as mobile_router
from jarvis.mobile.rtc_proxy import router as rtc_router
from jarvis.whatsapp.api import router as whatsapp_router

logger = logging.getLogger("jarvis")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: object) -> Response:
        response: Response = await call_next(request)  # type: ignore[operator]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.paths.log_dir)
    state = AppState(settings)
    _, mcp_manager = build_mcp(state)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp_manager.run():
            try:
                await upgrade_to_head()
            except Exception as e:
                logger.exception("Database migration failed")
                state.degraded = f"Datenbank-Migration fehlgeschlagen: {type(e).__name__}"
            await state.start()
            if settings.mobile.enabled and settings.mobile.gateway.enabled:
                await state.gateway.start(app)
            logger.info("J.A.R.V.I.S. core %s listening on %s:%s", VERSION, settings.server.host, settings.server.port)
            try:
                yield
            finally:
                await state.gateway.stop()
                await state.stop()
                await dispose_engine()

    app = FastAPI(
        title="J.A.R.V.I.S. Core",
        version=VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.jarvis = state
    app.add_middleware(SecurityHeaders)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    # outermost: tunnel traffic only reaches /api/telephony/*, the mobile gateway port only mobile paths
    app.add_middleware(
        PublicExposureGuard, mobile_port=settings.mobile.gateway.port, remote_api=settings.clients.remote_api
    )
    app.include_router(api_router)
    app.include_router(devices_router)
    app.include_router(realtime_router)
    app.include_router(voice_router)
    app.include_router(mobile_router)
    app.include_router(rtc_router)
    app.include_router(whatsapp_router)
    # A Route (not Mount) so that "/mcp" is served without a trailing-slash redirect.
    app.router.routes.append(Route("/mcp", endpoint=MCPAuthApp(mcp_manager), methods=["GET", "POST", "DELETE"]))
    if settings.clients.web_client:
        mount_web_client(app, settings)
    return app


def mount_web_client(app: FastAPI, settings: Settings) -> None:
    """Browser client: the desktop UI build served at /app (same origin as the API, sign-in with a device token)."""
    from starlette.staticfiles import StaticFiles

    from jarvis.config import REPO_ROOT

    directory = settings.clients.web_client_dir or REPO_ROOT / "desktop" / "dist"
    if not (directory / "index.html").is_file():
        logger.warning("Web client enabled, but %s has no build (cd desktop && pnpm build)", directory)
        return
    app.mount("/app", StaticFiles(directory=directory, html=True), name="web-client")


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.server.host,
        port=settings.server.port,
        log_level="info",
        access_log=False,
        ws_ping_interval=20,
        ws_max_size=MAX_FRAME_BYTES,  # device agents send screenshots over their WebSocket
        proxy_headers=False,
    )


if __name__ == "__main__":
    run()
