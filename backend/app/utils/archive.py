"""Safe inspection and extraction of uploaded ZIP archives.

Uploaded archives are hostile input by default. Before a single byte is
extracted, :func:`inspect_zip` rejects:

* non-ZIP payloads and corrupt central directories,
* member names that could escape the extraction directory (``../``, absolute
  paths, drive letters, NUL bytes),
* symbolic links (a symlink can point anywhere on the filesystem),
* encrypted members,
* entries whose uncompressed size, total expanded size or compression ratio
  exceed the configured limits (protection against "zip bombs"),
* archives with more members than the configured maximum.

Extraction streams each member and verifies the number of bytes written against
the size declared in the archive header, so a forged header cannot smuggle a
larger payload past the limits.
"""

from __future__ import annotations

import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.exceptions import ArchiveSecurityError, InvalidArchiveError
from app.core.logging import get_logger
from app.utils.paths import is_safe_member_name, safe_join

logger = get_logger(__name__)

ZIP_LOCAL_HEADER_MAGIC = b"PK\x03\x04"
ZIP_EMPTY_ARCHIVE_MAGIC = b"PK\x05\x06"
ZIP_SPANNED_MAGIC = b"PK\x07\x08"
ZIP_MAGICS = (ZIP_LOCAL_HEADER_MAGIC, ZIP_EMPTY_ARCHIVE_MAGIC, ZIP_SPANNED_MAGIC)

CHUNK_SIZE = 1024 * 1024

#: Directory/file names created by macOS when zipping; ignored rather than
#: rejected because they are metadata, not payload.
_IGNORED_PREFIXES = ("__MACOSX/",)
_IGNORED_NAMES = {".DS_Store", "Thumbs.db"}


@dataclass(frozen=True)
class ArchiveMember:
    """A single entry of an inspected archive."""

    name: str
    basename: str
    extension: str
    size: int
    compressed_size: int
    is_directory: bool

    @property
    def ignored(self) -> bool:
        return self.name.startswith(_IGNORED_PREFIXES) or self.basename in _IGNORED_NAMES


@dataclass(frozen=True)
class ArchiveInspection:
    """Result of a successful safety inspection."""

    members: tuple[ArchiveMember, ...]
    total_uncompressed_bytes: int

    @property
    def file_members(self) -> tuple[ArchiveMember, ...]:
        """Members that carry payload (no directories, no OS metadata)."""
        return tuple(m for m in self.members if not m.is_directory and not m.ignored)


def _extension_of(basename: str) -> str:
    """Return the lowercase suffix, keeping ``.shp.xml`` intact."""
    lower = basename.lower()
    if lower.endswith(".shp.xml"):
        return ".shp.xml"
    return PurePosixPath(lower).suffix


def _looks_like_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return bool(mode) and stat.S_ISLNK(mode)


def inspect_zip(
    archive_path: Path,
    *,
    max_entries: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: int,
    max_member_bytes: int,
) -> ArchiveInspection:
    """Validate an archive's structure against the configured safety limits."""
    if not zipfile.is_zipfile(archive_path):
        raise InvalidArchiveError(
            "The uploaded file is not a readable ZIP archive.",
            details={"reason": "not_a_zip_file"},
        )

    members: list[ArchiveMember] = []
    total_uncompressed = 0

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("zip_open_failed", extra={"error": type(exc).__name__})
        raise InvalidArchiveError(
            "The uploaded ZIP archive is corrupt.",
            details={"reason": "corrupt_archive"},
        ) from exc

    if not infos:
        raise InvalidArchiveError(
            "The uploaded ZIP archive is empty.", details={"reason": "empty_archive"}
        )

    if len(infos) > max_entries:
        raise ArchiveSecurityError(
            "The archive contains too many entries.",
            details={"entries": len(infos), "limit": max_entries},
        )

    for info in infos:
        if not is_safe_member_name(info.filename):
            logger.warning("unsafe_archive_member_rejected")
            raise ArchiveSecurityError(
                "The archive contains an entry with an unsafe path.",
                details={"member": info.filename, "reason": "unsafe_path"},
            )

        if _looks_like_symlink(info):
            raise ArchiveSecurityError(
                "The archive contains a symbolic link entry.",
                details={"member": info.filename, "reason": "symlink"},
            )

        if info.flag_bits & 0x1:
            raise ArchiveSecurityError(
                "Encrypted archives are not supported.",
                details={"member": info.filename, "reason": "encrypted"},
            )

        is_directory = info.is_dir()
        basename = PurePosixPath(info.filename.rstrip("/")).name

        if is_directory:
            members.append(
                ArchiveMember(
                    name=info.filename,
                    basename=basename,
                    extension="",
                    size=0,
                    compressed_size=0,
                    is_directory=True,
                )
            )
            continue

        if info.file_size > max_member_bytes:
            raise ArchiveSecurityError(
                "The archive contains an entry that is too large.",
                details={
                    "member": info.filename,
                    "size_bytes": info.file_size,
                    "limit_bytes": max_member_bytes,
                    "reason": "member_too_large",
                },
            )

        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > max_compression_ratio:
            raise ArchiveSecurityError(
                "The archive contains an entry with a suspicious compression ratio.",
                details={
                    "member": info.filename,
                    "ratio": round(ratio, 2),
                    "limit": max_compression_ratio,
                    "reason": "compression_ratio",
                },
            )

        total_uncompressed += info.file_size
        if total_uncompressed > max_uncompressed_bytes:
            raise ArchiveSecurityError(
                "The archive expands to more data than allowed.",
                details={
                    "uncompressed_bytes": total_uncompressed,
                    "limit_bytes": max_uncompressed_bytes,
                    "reason": "expanded_size",
                },
            )

        members.append(
            ArchiveMember(
                name=info.filename,
                basename=basename,
                extension=_extension_of(basename),
                size=info.file_size,
                compressed_size=info.compress_size,
                is_directory=False,
            )
        )

    return ArchiveInspection(
        members=tuple(members), total_uncompressed_bytes=total_uncompressed
    )


def extract_archive(
    archive_path: Path, destination: Path, inspection: ArchiveInspection
) -> list[Path]:
    """Extract an inspected archive into ``destination``.

    Member paths are validated again here (defence in depth) and each member's
    written byte count is compared against the header's declared size.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in inspection.members:
                if member.is_directory or member.ignored:
                    continue

                parts = [p for p in member.name.replace("\\", "/").split("/") if p not in ("", ".")]
                if not parts or any(part == ".." for part in parts):
                    raise ArchiveSecurityError(
                        "The archive contains an entry with an unsafe path.",
                        details={"member": member.name},
                    )

                try:
                    target = safe_join(destination, *parts)
                except ValueError as exc:
                    raise ArchiveSecurityError(
                        "The archive contains an entry that escapes the extraction directory.",
                        details={"member": member.name},
                    ) from exc

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member.name) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink, length=CHUNK_SIZE)
                    sink.flush()

                actual_size = target.stat().st_size
                if actual_size != member.size:
                    raise ArchiveSecurityError(
                        "The archive declares a size that does not match its contents.",
                        details={
                            "member": member.name,
                            "declared_bytes": member.size,
                            "actual_bytes": actual_size,
                        },
                    )
                written.append(target)
    except ArchiveSecurityError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise InvalidArchiveError(
            "The uploaded ZIP archive could not be extracted.",
            details={"reason": "extraction_failed"},
        ) from exc

    return written
