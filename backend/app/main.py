"""FastAPI application factory and entry point.

The application is assembled in :func:`create_app` so it can be imported by the
test suite and executed directly (``python -m app.main``).  Startup and
shutdown events own two lifecycle concerns that must never leak between runs:

* the logging configuration is installed once per process and rebuilt on every
  startup (tests import the module repeatedly);
* the database engines are created lazily and disposed when the process exits.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    DatabaseUnavailableError,
    register_exception_handlers,
)
from app.core.logging import configure_logging, get_logger
from app.db.session import check_database_connection, dispose_engines, get_db
from app.routes import api_router
from app.schemas.common import HealthResponse, ReadinessResponse

logger = get_logger(__name__)


def _configure_logging(settings: Settings) -> None:
    """Install the logging configuration for the current process."""
    configure_logging(level=settings.log_level, fmt=settings.log_format)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Bootstrap the process and tear it down cleanly."""
    settings = get_settings()
    _configure_logging(settings)
    storage_root = settings.ensure_storage_root()
    logger.info(
        "application_starting",
        extra={"app_name": settings.app_name, "storage_root": str(storage_root)},
    )
    app.state.settings = settings
    app.state.storage_root = storage_root
    app.state.max_upload_size_bytes = settings.max_upload_size_bytes
    yield
    dispose_engines()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the :class:`FastAPI` application."""
    settings = settings or get_settings()

    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=_lifespan)

    # --- Exception handlers (registered before routes so they win) ---------- #
    register_exception_handlers(app)

    # --- CORS -------------------------------------------------------------- #
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # --- Routers ------------------------------------------------------------ #
    app.include_router(api_router, prefix=settings.api_prefix)

    # --- Health / readiness endpoints (public, no dependencies) ------------- #
    @app.get("/health/", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app_name=settings.app_name,
            version=app.version,
            environment=settings.app_env,
        )

    @app.get("/health/readiness", response_model=ReadinessResponse, tags=["meta"])
    async def readiness() -> ReadinessResponse:
        database_ok = check_database_connection()
        checks = [
            {"name": "database", "healthy": database_ok, "detail": None},
        ]
        status = "ready" if all(c["healthy"] for c in checks) else "degraded"
        return ReadinessResponse(status=status, checks=checks)

    return app


app = create_app()

if __name__ == "__main__":  # pragma: no cover - manual execution
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
