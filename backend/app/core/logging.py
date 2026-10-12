"""Structured application logging.

Two output formats are supported:

* ``json``    - one JSON object per line; intended for production log shipping.
* ``console`` - human readable, used for local development.

Every log record may carry a ``request_id`` (propagated through a
:class:`contextvars.ContextVar` set by the request-ID middleware) so all lines
belonging to a single HTTP request can be correlated.

The logger deliberately never receives uploaded file *contents*; callers log
identifiers, sizes and error categories instead (see README "Observability").
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

#: Correlates every log line emitted while handling one HTTP request.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

#: Extra attributes that exist on every ``LogRecord``; used by the JSON
#: formatter to decide which attributes are user supplied ("extra").
_RESERVED_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_REDACTED_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "database_url",
        "dsn",
    }
)


def _redact(value: Any) -> Any:
    """Best-effort redaction of obviously sensitive keys in ``extra``."""
    if isinstance(value, dict):
        return {
            key: ("***redacted***" if str(key).lower() in _REDACTED_KEYS else _redact(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib API
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None) or request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key.startswith("_"):
                continue
            payload[key] = _redact(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human readable formatter with optional request correlation."""

    default_time_format = "%Y-%m-%dT%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib API
        request_id = getattr(record, "request_id", None) or request_id_ctx.get()
        prefix = f"[{request_id[:8]}] " if request_id else ""
        base = (
            f"{self.formatTime(record, self.default_time_format)} "
            f"{record.levelname:<8} {record.name} {prefix}{record.getMessage()}"
        )
        extras = {
            key: _redact(value)
            for key, value in record.__dict__.items()
            if key not in _RESERVED_ATTRS and not key.startswith("_")
        }
        if extras:
            base += " " + " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root logging configuration.

    Idempotent: calling it twice replaces the handlers instead of stacking them
    (important because tests import the application repeatedly).
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())

    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; delegate to the root configuration so
    # access and error logs share one format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # SQLAlchemy is chatty at INFO; keep its engine logs at WARNING unless
    # DEBUG is explicitly requested via `db_echo` / LOG_LEVEL=DEBUG.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG if level.upper() == "DEBUG" else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module logger."""
    return logging.getLogger(name)
