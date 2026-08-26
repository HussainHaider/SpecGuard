"""The FastAPI application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from specguard.api.metrics import CHECKS_TOTAL
from specguard.api.routers import router
from specguard.config import get_settings
from specguard.logging import bind_correlation_id, configure_logging, get_logger
from specguard.tracing import configure_tracing

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the queue connection on startup and close it on shutdown.

    The API stays up when Redis is unavailable — it just cannot queue. That is a
    deliberate choice: GET /checks/{id} and the report it returns are still useful when
    the queue is down, and a health endpoint that reports the problem is more use than a
    process that refuses to start.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    if configure_tracing(settings):
        log.info("tracing.enabled", project=settings.langsmith_project)
    try:
        app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        log.info("queue.connected")
    except Exception as error:
        app.state.queue = None
        log.error("queue.unavailable", error=str(error))
    yield
    if app.state.queue is not None:
        await app.state.queue.aclose()


def create_app() -> FastAPI:
    """Build the application."""
    app = FastAPI(
        title="SpecGuard",
        version="0.1.0",
        summary="Per-rule EU food law compliance checks over supplier specification sheets.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Any) -> Any:
        """Bind a correlation id for the request and echo it back on the response."""
        correlation_id = bind_correlation_id(request.headers.get("x-correlation-id"))
        response = await call_next(request)
        response.headers["x-correlation-id"] = correlation_id
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, error: Exception) -> JSONResponse:
        """Log the detail, return none of it.

        An unhandled error's message can carry a connection string or a fragment of
        someone's specification. It goes to the log with the correlation id; the caller
        gets the id and nothing else.
        """
        del request
        CHECKS_TOTAL.labels(status="error").inc()
        log.exception("request.unhandled", error=str(error))
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal error.",
                "correlation_id": bind_correlation_id(None),
            },
        )

    app.include_router(router)
    return app


app = create_app()
