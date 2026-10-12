"""Physical storage of uploaded files.

Layout (``relative_path`` values stored in the database are always POSIX-style
so they remain valid if the storage root is relocated)::

    <storage_root>/
        <file_id>/                 # one directory per upload, id is a UUID4
            source/<name>          # the uploaded bytes (name sanitised)
            extracted/             # temporary ZIP expansion, always removed

Design notes
------------
* The client filename never determines where bytes land: the directory is
  derived from the server-generated UUID and the leaf name is sanitised.
* Uploads are streamed in chunks, so an oversized body is rejected without
  being held in memory, and the SHA-256 is computed on the fly.
* Writes go to a ``.part`` file that is renamed into place, so a partially
  written upload can never be mistaken for a complete one.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.exceptions import FileTooLargeError, StorageError
from app.core.logging import get_logger
from app.utils.paths import ensure_directory, safe_join, sanitize_filename

logger = get_logger(__name__)

CHUNK_SIZE = 1024 * 1024
SOURCE_DIRNAME = "source"
EXTRACT_DIRNAME = "extracted"


class AsyncByteStream(Protocol):
    """Minimal interface of an uploaded body (satisfied by UploadFile)."""

    async def read(self, size: int = ...) -> bytes: ...


@dataclass(frozen=True)
class StoredFile:
    """Description of a file that has been written to disk."""

    file_id: uuid.UUID
    original_filename: str
    relative_path: str
    absolute_path: Path
    size_bytes: int
    checksum_sha256: str

    @property
    def extension(self) -> str:
        return Path(self.original_filename).suffix.lower()


class FileStorageService:
    """Writes, locates and removes uploaded files."""

    def __init__(self, root: Path, max_upload_size_bytes: int) -> None:
        self.root = Path(root)
        self.max_upload_size_bytes = max_upload_size_bytes

    # ------------------------------------------------------------------ #
    # Paths
    # ------------------------------------------------------------------ #
    def file_directory(self, file_id: uuid.UUID | str) -> Path:
        return ensure_directory(safe_join(self.root, str(file_id)))

    def source_path(self, file_id: uuid.UUID | str, stored_name: str) -> Path:
        directory = ensure_directory(self.file_directory(file_id) / SOURCE_DIRNAME)
        return safe_join(directory, stored_name)

    def extraction_directory(self, file_id: uuid.UUID | str) -> Path:
        return ensure_directory(self.file_directory(file_id) / EXTRACT_DIRNAME)

    def absolute_path(self, relative_path: str) -> Path:
        """Resolve a stored relative path, refusing anything outside the root."""
        root = self.root.resolve()
        candidate = (root / relative_path).resolve()
        if candidate != root and root not in candidate.parents:
            raise StorageError("Stored path is outside the storage root.")
        return candidate

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #
    async def save_upload(
        self, upload: AsyncByteStream, original_filename: str
    ) -> StoredFile:
        """Stream ``upload`` to disk under a freshly generated identifier."""
        file_id = uuid.uuid4()
        display_name = sanitize_filename(original_filename)
        target = self.source_path(file_id, display_name)
        partial = target.with_name(f"{target.name}.part")

        digest = hashlib.sha256()
        size = 0

        try:
            with partial.open("wb") as sink:
                while True:
                    chunk = await upload.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_upload_size_bytes:
                        raise FileTooLargeError(
                            details={
                                "size_bytes": size,
                                "limit_bytes": self.max_upload_size_bytes,
                            }
                        )
                    digest.update(chunk)
                    sink.write(chunk)

            if size == 0:
                raise StorageError("The uploaded file is empty.")

            partial.replace(target)
        except FileTooLargeError:
            self._discard(partial, file_id)
            raise
        except StorageError:
            self._discard(partial, file_id)
            raise
        except OSError as exc:
            self._discard(partial, file_id)
            logger.error("upload_write_failed", extra={"error": type(exc).__name__})
            raise StorageError("The uploaded file could not be written to disk.") from exc

        relative = f"{file_id}/{SOURCE_DIRNAME}/{display_name}"
        logger.info(
            "upload_stored",
            extra={"file_id": str(file_id), "size_bytes": size},
        )
        return StoredFile(
            file_id=file_id,
            original_filename=original_filename,
            relative_path=relative,
            absolute_path=target,
            size_bytes=size,
            checksum_sha256=digest.hexdigest(),
        )

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #
    def cleanup_extraction(self, file_id: uuid.UUID | str) -> None:
        """Remove the temporary extraction directory (always called)."""
        directory = self.root / str(file_id) / EXTRACT_DIRNAME
        shutil.rmtree(directory, ignore_errors=True)

    def delete_file(self, file_id: uuid.UUID | str) -> bool:
        """Remove every artefact belonging to ``file_id``.

        Returns ``True`` when a directory existed. Missing files are not an
        error: a record may legitimately outlive its bytes (for example when an
        operator removed them manually).
        """
        directory = self.root / str(file_id)
        if not directory.exists():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True

    def _discard(self, partial: Path, file_id: uuid.UUID) -> None:
        partial.unlink(missing_ok=True)
        shutil.rmtree(self.root / str(file_id), ignore_errors=True)
