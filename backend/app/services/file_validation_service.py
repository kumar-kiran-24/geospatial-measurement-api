"""Validation of uploaded files.

The extension is treated as a *hint*, never as proof. Detection has two stages:

1. **Extension allow-list** - cheap rejection of obviously unsupported uploads.
2. **Content sniffing** - the first bytes on disk must actually look like the
   claimed container (ZIP ``PK`` magic or KML/XML markup). A ``.kml`` file that
   is really a ZIP (i.e. a KMZ) or a ``.zip`` that is really XML is rejected
   with an explicit mismatch error instead of being handed to the parser.

For archives a third stage validates the Shapefile *dataset*: exactly one
``.shp`` together with its required ``.shx`` and ``.dbf`` siblings, and no
unexpected file types.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import (
    SHAPEFILE_OPTIONAL_EXTENSIONS,
    SHAPEFILE_REQUIRED_EXTENSIONS,
    Settings,
)
from app.core.exceptions import InvalidArchiveError, UnsupportedFileTypeError
from app.core.logging import get_logger
from app.models.enums import FileFormat
from app.utils.archive import ZIP_LOCAL_HEADER_MAGIC, ArchiveInspection

logger = get_logger(__name__)

SNIFF_BYTES = 8192

#: Markers that identify a KML document regardless of XML namespace prefix.
_KML_MARKERS = (b"<kml", b"opengis.net/kml")

#: Shapefile sidecar extensions that are accepted inside an archive.
_ALLOWED_SIDECARS = SHAPEFILE_REQUIRED_EXTENSIONS | SHAPEFILE_OPTIONAL_EXTENSIONS


@dataclass(frozen=True)
class ShapefileDataset:
    """A complete Shapefile dataset found inside an archive."""

    basename: str
    member_names: tuple[str, ...]

    @property
    def shp_extension(self) -> str:
        return ".shp"


class FileValidationService:
    """Format detection and archive content validation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ #
    # Extension + content
    # ------------------------------------------------------------------ #
    def validate_extension(self, filename: str) -> str:
        """Return the lowercased extension after checking the allow-list."""
        extension = Path(filename or "").suffix.lower()
        if not extension:
            raise UnsupportedFileTypeError(
                "The uploaded file has no extension.",
                details={"allowed_extensions": sorted(self.settings.allowed_extensions)},
            )
        if extension not in self.settings.allowed_extensions:
            raise UnsupportedFileTypeError(
                details={
                    "extension": extension,
                    "allowed_extensions": sorted(self.settings.allowed_extensions),
                },
            )
        return extension

    def sniff(self, path: Path, extension: str) -> FileFormat:
        """Determine the real container format from a bounded read of the file."""
        try:
            with Path(path).open("rb") as handle:
                head = handle.read(SNIFF_BYTES)
        except OSError as exc:  # pragma: no cover - storage layer guarantees a file
            logger.error("sniff_read_failed", extra={"error": type(exc).__name__})
            raise UnsupportedFileTypeError(
                "The uploaded file could not be read.",
                details={"reason": "unreadable"},
            ) from exc

        if not head:
            raise UnsupportedFileTypeError(
                "The uploaded file is empty.",
                details={"reason": "empty_file"},
            )

        looks_zip = head.startswith(ZIP_LOCAL_HEADER_MAGIC)
        lowered = head.lower()
        looks_kml = any(marker in lowered for marker in _KML_MARKERS)

        if extension == ".zip":
            if looks_zip:
                return FileFormat.SHAPEFILE
            raise UnsupportedFileTypeError(
                "The uploaded .zip file does not contain a ZIP archive.",
                code="content_extension_mismatch",
                details={"expected": "zip", "detected": "kml" if looks_kml else "unknown"},
            )

        if looks_zip:
            # A .kml that is really an archive is a KMZ, which we do not accept.
            raise UnsupportedFileTypeError(
                "The uploaded .kml file is a compressed KML (KMZ), which is not supported. "
                "Upload an uncompressed .kml file or a zipped Shapefile.",
                code="unsupported_file_type",
                details={"expected": "kml", "detected": "zip"},
            )

        if looks_kml:
            return FileFormat.KML

        raise UnsupportedFileTypeError(
            "The uploaded file is not recognisable as KML or a ZIP archive.",
            code="unrecognised_content",
            details={"extension": extension},
        )

    def detect_format(self, path: Path, filename: str) -> FileFormat:
        """Validate the extension and return the content-detected format."""
        extension = self.validate_extension(filename)
        return self.sniff(path, extension)

    # ------------------------------------------------------------------ #
    # Shapefile archive content
    # ------------------------------------------------------------------ #
    def validate_shapefile_archive(self, inspection: ArchiveInspection) -> ShapefileDataset:
        """Ensure the archive holds exactly one complete Shapefile dataset."""
        members = inspection.file_members
        if not members:
            raise InvalidArchiveError(
                "The uploaded ZIP archive contains no files.",
                details={"reason": "no_files"},
            )

        unexpected = sorted(
            {member.basename for member in members if member.extension not in _ALLOWED_SIDECARS}
        )
        if unexpected:
            raise InvalidArchiveError(
                "The uploaded ZIP archive contains files that are not part of a Shapefile dataset.",
                code="invalid_archive",
                details={
                    "unexpected_files": unexpected,
                    "allowed_extensions": sorted(_ALLOWED_SIDECARS),
                },
            )

        # Group members by their dataset basename (the file name without suffix).
        groups: dict[str, dict[str, str]] = {}
        for member in members:
            extension = member.extension
            if extension == ".shp.xml":
                basename = member.basename[: -len(".shp.xml")]
                extension = ".shp.xml"
            else:
                basename = member.basename[: -len(extension)] if extension else member.basename
            groups.setdefault(basename, {})[extension] = member.name

        datasets = {basename: exts for basename, exts in groups.items() if ".shp" in exts}
        if not datasets:
            raise InvalidArchiveError(
                "The uploaded ZIP archive does not contain a .shp file.",
                details={"reason": "missing_shp"},
            )
        if len(datasets) > 1:
            raise InvalidArchiveError(
                "The uploaded ZIP archive contains more than one Shapefile dataset.",
                details={
                    "reason": "ambiguous_dataset",
                    "candidates": sorted(datasets),
                },
            )

        basename, extensions = next(iter(datasets.items()))
        missing = sorted(SHAPEFILE_REQUIRED_EXTENSIONS - set(extensions))
        if missing:
            raise InvalidArchiveError(
                "The Shapefile dataset is incomplete.",
                details={
                    "reason": "missing_sidecar_files",
                    "basename": basename,
                    "missing": missing,
                    "present": sorted(extensions),
                },
            )

        logger.info(
            "shapefile_archive_validated",
            extra={"members": sorted(extensions), "basename": basename},
        )
        return ShapefileDataset(
            basename=basename,
            member_names=tuple(sorted(member.name for member in members)),
        )

    def find_shp_file(self, directory: Path) -> Path:
        """Locate the single ``.shp`` file in an extracted directory."""
        candidates = sorted(
            path
            for path in Path(directory).rglob("*")
            if path.is_file() and path.suffix.lower() == ".shp"
        )
        if not candidates:
            raise InvalidArchiveError(
                "The extracted archive does not contain a .shp file.",
                details={"reason": "missing_shp"},
            )
        if len(candidates) > 1:
            raise InvalidArchiveError(
                "The extracted archive contains more than one .shp file.",
                details={"reason": "ambiguous_dataset", "candidates": [c.name for c in candidates]},
            )
        return candidates[0]
