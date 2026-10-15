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

from jarvis.api.realtime import router as realtime_router
from jarvis.api.routes import router as api_router
from jarvis.app_state import VERSION, AppState
from jarvis.config import Settings, get_settings
from jarvis.db import dispose_engine
from jarvis.logging_setup import setup_logging
from jarvis.mcp.server import MCPAuthApp, build_mcp

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
            await state.start()
            logger.info("J.A.R.V.I.S. core %s listening on %s:%s", VERSION, settings.server.host, settings.server.port)
            try:
                yield
            finally:
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
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(api_router)
    app.include_router(realtime_router)
    # A Route (not Mount) so that "/mcp" is served without a trailing-slash redirect.
    app.router.routes.append(Route("/mcp", endpoint=MCPAuthApp(mcp_manager), methods=["GET", "POST", "DELETE"]))
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.server.host,
        port=settings.server.port,
        log_level="info",
        access_log=False,
        ws_ping_interval=20,
        proxy_headers=False,
    )


if __name__ == "__main__":
    run()
