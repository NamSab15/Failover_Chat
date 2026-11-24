"""FastAPI application factory.

Every error is JSON with a "detail" field — never a stack trace or an HTML
page. SQLite schema is created automatically on startup.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .db import Database, resolve_sqlite_path
from .gateway import LLMGateway
from .routes import router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(resolve_sqlite_path(settings.database_url))
    gateway = LLMGateway(settings)
    if gateway.key_count < 2:
        logger.warning(
            "Only %d key(s) configured; the assignment requires a pool of at "
            "least 2 (LLM_API_KEYS=key1,key2,...).",
            gateway.key_count,
        )

    app = FastAPI(
        title="LLM Chatbot — Key Failover & Response Caching",
        description="FastAPI + LiteLLM chatbot with a key pool, automatic "
        "failover, response caching and SQLite session storage.",
        version="1.0.0",
    )

    app.state.settings = settings
    app.state.db = db
    app.state.gateway = gateway

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins or ["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],  # includes X-Simulate-Failure for browser demos
    )

    app.include_router(router)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    # 422 validation errors keep FastAPI's default shape: {"detail": [...]}.

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Last-resort guard: the user must never see a stack trace.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    return app


app = create_app()
