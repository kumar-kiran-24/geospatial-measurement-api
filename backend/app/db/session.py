"""Engine and session management.

Design notes
------------
* One :class:`~sqlalchemy.engine.Engine` is created per database URL and cached,
  so tests can point at a different database without leaking connections.
* ``pool_pre_ping`` guards against stale connections (common with container
  restarts and managed databases).
* Sessions are never committed implicitly by the dependency. Services decide
  the transaction boundary; the dependency guarantees rollback + close.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_ENGINES: dict[str, Engine] = {}
_SESSION_FACTORIES: dict[str, sessionmaker[Session]] = {}


def _engine_kwargs(url: str) -> dict[str, object]:
    settings = get_settings()
    kwargs: dict[str, object] = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,
        "future": True,
    }
    # SQLite (used only by a few unit tests) has no connection pool knobs.
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        return kwargs
    kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
    )
    return kwargs


def get_engine(url: str | None = None) -> Engine:
    """Return a cached engine for ``url`` (defaults to the configured URL)."""
    target = url or get_settings().effective_database_url
    engine = _ENGINES.get(target)
    if engine is None:
        engine = create_engine(target, **_engine_kwargs(target))
        _ENGINES[target] = engine
    return engine


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Return a cached session factory for ``url``."""
    target = url or get_settings().effective_database_url
    factory = _SESSION_FACTORIES.get(target)
    if factory is None:
        factory = sessionmaker(
            bind=get_engine(target),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        _SESSION_FACTORIES[target] = factory
    return factory


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Transactional scope for background/script usage (commit or rollback)."""
    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to the request lifecycle."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> bool:
    """Return ``True`` when a trivial query succeeds (used by /health/ready)."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 - health check must never raise
        logger.warning("database_readiness_check_failed", extra={"error": repr(exc)})
        return False


def dispose_engines() -> None:
    """Dispose all cached engines (called on application shutdown)."""
    for url, engine in list(_ENGINES.items()):
        engine.dispose()
        _ENGINES.pop(url, None)
        _SESSION_FACTORIES.pop(url, None)
    _ENGINES.clear()
    _SESSION_FACTORIES.clear()
