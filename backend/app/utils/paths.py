"""Filesystem safety helpers.

Uploaded filenames and archive member names are **untrusted input**. Nothing in
this module ever uses them to build a path without going through
:func:`safe_join`, which proves the resolved target stays inside the designated
root directory.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

#: Characters allowed in a name used for display or as a file name component.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

MAX_FILENAME_LENGTH = 200
FALLBACK_FILENAME = "upload"


def sanitize_filename(name: str | None) -> str:
    """Return a display-safe version of a client-supplied filename.

    The result is only ever used for *display* and as a leaf name inside a
    server-generated directory; it is never trusted to describe a location.
    """
    if not name:
        return FALLBACK_FILENAME

    # Strip any directory component (POSIX and Windows separators alike).
    candidate = name.replace("\\", "/").split("/")[-1]
    # Remove control characters and normalise unicode (defeats homoglyph tricks).
    candidate = _CONTROL_CHARS.sub("", unicodedata.normalize("NFKC", candidate))
    candidate = candidate.strip().strip(".")
    candidate = _UNSAFE_CHARS.sub("_", candidate)
    candidate = candidate.strip()

    if not candidate:
        return FALLBACK_FILENAME
    return candidate[:MAX_FILENAME_LENGTH]


def is_safe_member_name(member_name: str) -> bool:
    """Reject archive member names that could escape the extraction directory.

    Blocks absolute paths, drive letters, ``..`` traversal, UNC paths and NUL
    bytes. The check is deliberately performed on the *raw* name so that no
    normalisation step can hide a traversal.
    """
    if not member_name or "\x00" in member_name:
        return False

    normalised = member_name.replace("\\", "/")

    if normalised.startswith("/"):  # absolute POSIX path
        return False
    if re.match(r"^[A-Za-z]:", normalised):  # Windows drive letter
        return False
    if normalised.startswith("//"):  # UNC path
        return False

    parts = [part for part in normalised.split("/") if part not in ("", ".")]
    return all(part != ".." for part in parts)


def safe_join(root: Path, *parts: str) -> Path:
    """Join ``parts`` onto ``root``, guaranteeing the result stays inside root.

    Raises :class:`ValueError` when the resolved path escapes the root, which
    the caller translates into a client-facing security error.
    """
    root_resolved = Path(root).resolve()
    candidate = root_resolved.joinpath(*parts).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"path escapes storage root: {candidate}")
    return candidate


def ensure_directory(path: Path) -> Path:
    """Create ``path`` (and parents) if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
