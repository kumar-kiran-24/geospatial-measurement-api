"""Centralised exception hierarchy and FastAPI exception handlers.

Two rules drive this module:

1. **Clients never see internals.** Stack traces, SQL statements, filesystem
   paths and GDAL driver messages are logged server-side but replaced with a
   stable ``code`` and a safe human-readable ``message`` in the response.
2. **Every failure carries a machine-readable code** so clients switch on
   ``error.code`` instead of parsing prose.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_ctx

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for every expected, client-safe application error."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


# --------------------------------------------------------------------------- #
# Concrete errors
# --------------------------------------------------------------------------- #
class ValidationFailure(AppError):
    """Request payload is syntactically valid but semantically rejected."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "validation_error"
    message = "The request could not be validated."


class UnsupportedFileTypeError(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_file_type"
    message = "Only .kml files and .zip archives containing a Shapefile are supported."


class FileTooLargeError(AppError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    code = "file_too_large"
    message = "The uploaded file exceeds the configured maximum size."


class InvalidArchiveError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid_archive"
    message = "The uploaded ZIP archive is invalid or does not contain a Shapefile dataset."


class ArchiveSecurityError(AppError):
    """Path traversal, symlinks, entry bombs or unexpected member types."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "unsafe_archive"
    message = "The uploaded ZIP archive was rejected by the safety checks."


class GeospatialParseError(AppError):
    """The file is a supported container but its geospatial content is unusable."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "geospatial_parse_error"
    message = "The geospatial file could not be parsed."


class EmptyDatasetError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "empty_dataset"
    message = "The geospatial file does not contain any features."


class TooManyFeaturesError(AppError):
    """Guard rail: the dataset is valid but larger than this deployment allows."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "too_many_features"
    message = "The geospatial file contains more features than this deployment allows."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found."


class ProcessingError(AppError):
    """A file-level processing failure that is safe to describe to the client."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "processing_failed"
    message = "The uploaded file could not be processed."


class StorageError(AppError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "storage_error"
    message = "The file could not be stored."


class DatabaseUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "database_unavailable"
    message = "The database is currently unavailable."


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _request_id() -> str | None:
    return request_id_ctx.get()


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler to ``app``. Called from :func:`app.main.create_app`."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        payload = exc.to_payload()
        payload["error"]["request_id"] = _request_id()
        # 4xx are client problems and logged quietly; 5xx indicate a server
        # fault and are logged with a stack trace.
        if exc.status_code >= 500:
            logger.error(
                "application_error", extra={"code": exc.code, "details": exc.details}
            )
        else:
            logger.info(
                "request_rejected", extra={"code": exc.code, "details": exc.details}
            )
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = {
            "fields": [
                {
                    "location": ".".join(str(part) for part in err.get("loc", ())),
                    "message": err.get("msg", "invalid value"),
                    "type": err.get("type", "value_error"),
                }
                for err in exc.errors()
            ]
        }
        logger.info("request_validation_failed", extra={"fields": details["fields"]})
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "request_validation_error",
                    "message": "The request payload failed validation.",
                    "details": details,
                    "request_id": _request_id(),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
            401: "unauthorized",
            403: "forbidden",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                    "request_id": _request_id(),
                }
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _handle_db_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database_error", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "database_unavailable",
                    "message": "The database is currently unavailable.",
                    "request_id": _request_id(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Log the full trace server-side; return an opaque message to the client.
        logger.exception("unhandled_exception", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "request_id": _request_id(),
                }
            },
        )
