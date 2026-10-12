"""Application configuration.

All runtime configuration is read from environment variables (optionally via a
``.env`` file) so the same image can run in development, test and production.

The module exposes :func:`get_settings`, a cached accessor. Tests override
configuration by mutating environment variables and calling
:func:`get_settings.cache_clear` (see ``tests/conftest.py``).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_ALLOWED_EXTENSIONS = frozenset({".kml", ".zip"})

#: Extensions accepted inside an uploaded ZIP for a Shapefile dataset.
SHAPEFILE_REQUIRED_EXTENSIONS = frozenset({".shp", ".shx", ".dbf"})
SHAPEFILE_OPTIONAL_EXTENSIONS = frozenset(
    {".prj", ".cpg", ".qix", ".sbn", ".sbx", ".xml", ".shp.xml", ".fbn", ".fbx"}
)


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "Geospatial File Measurement API"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])

    # --- Database ---
    database_url: str = (
        "postgresql+psycopg://geo_app:geo_app@localhost:5432/geospatial_files"
    )
    test_database_url: str = (
        "postgresql+psycopg://geo_app:geo_app@localhost:5432/geospatial_files_test"
    )
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # --- Storage / uploads ---
    storage_root: Path = PROJECT_ROOT / "var" / "storage"
    max_upload_size_bytes: int = 25 * 1024 * 1024
    allowed_extensions: frozenset[str] = DEFAULT_ALLOWED_EXTENSIONS

    # --- Archive safety ---
    max_zip_entries: int = 64
    max_zip_uncompressed_bytes: int = 200 * 1024 * 1024
    max_zip_compression_ratio: int = 100
    max_archive_member_bytes: int = 100 * 1024 * 1024
    max_features_per_file: int = 100_000

    # --- Processing ---
    sync_processing: bool = True

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def _parse_extensions(cls, value: object) -> object:
        """Accept ``".kml,.zip"`` or a list and normalise to lowercase dots."""
        if value is None:
            return DEFAULT_ALLOWED_EXTENSIONS
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set, frozenset)):
            items = [str(item).strip() for item in value if str(item).strip()]
        else:  # pragma: no cover - defensive
            return value
        normalised = {
            item.lower() if item.startswith(".") else f".{item.lower()}"
            for item in items
        }
        return frozenset(normalised)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("api_prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        return value.rstrip("/") or "/"

    @field_validator("storage_root", mode="before")
    @classmethod
    def _resolve_storage_root(cls, value: object) -> object:
        """Resolve relative storage paths against the backend project root."""
        if value is None:
            return PROJECT_ROOT / "var" / "storage"
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    # ------------------------------------------------------------------ #
    # Derived properties
    # ------------------------------------------------------------------ #
    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def effective_database_url(self) -> str:
        """Use the dedicated test database when running the test suite."""
        return self.test_database_url if self.is_test else self.database_url

    def ensure_storage_root(self) -> Path:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        return self.storage_root


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
