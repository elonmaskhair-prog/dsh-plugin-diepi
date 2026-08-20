"""Small, transport-independent trust-boundary helpers."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import NoReturn

from pydantic import ValidationError


_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")
_RUN_ID_RE = re.compile(r"^dsh_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{12}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\)[^\r\n\t,;\]\[{}()<>]*"
)
_QUOTED_POSIX_PATH_RE = re.compile(
    r'''(?x)(?<![A-Za-z0-9:])(?:
        "(?:/[^"\r\n]*)"
        | '(?:/[^'\r\n]*)'
        | \u201c(?:/[^\u201d\r\n]*)\u201d
        | \u2018(?:/[^\u2019\r\n]*)\u2019
    )'''
)
_POSIX_PATH_RE = re.compile(
    # An unquoted POSIX path containing spaces has no reliable syntactic end.
    # Consume through the line or an explicit delimiter rather than disclosing
    # a filename or deeper path suffix.
    r'''(?x)(?<![A-Za-z0-9:])/
        [^\r\n,;\]\[{}()<>"'\u2018\u2019\u201c\u201d]*
    '''
)
_MAX_PUBLIC_ERROR_CHARS = 240


def require_job_id(value: object) -> str:
    """Return one canonical opaque job id before any DB or filesystem access."""

    if type(value) is not str or not _JOB_ID_RE.fullmatch(value):
        raise ValueError("job_id must match job_[0-9a-f]{32}")
    return value


def is_run_id(value: object) -> bool:
    return type(value) is str and _RUN_ID_RE.fullmatch(value) is not None


def _require_plain_directory_chain(root: Path, label: str) -> None:
    parts = root.parts
    if not parts:
        raise ValueError(f"{label} root is invalid")
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ValueError(f"{label} root cannot be inspected") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or current.is_symlink()
            or bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
        ):
            raise ValueError(f"{label} root must use plain directory components")


def confined_child(root: Path, name: str, label: str) -> Path:
    """Resolve an opaque child and reject path aliases escaping its trusted root."""

    configured_root = Path(root)
    if os.path.lexists(configured_root):
        _require_plain_directory_chain(configured_root, label)
        trusted_root = configured_root.resolve(strict=True)
    else:
        _require_plain_directory_chain(configured_root.parent, label)
        trusted_root = configured_root.resolve(strict=False)
    candidate = trusted_root / name
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(trusted_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its configured root") from exc
    if os.path.lexists(candidate):
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise ValueError(f"{label} cannot be inspected") from exc
        if candidate.is_symlink() or bool(
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError(f"{label} must not be a symbolic link or reparse point")
    return resolved


def sanitize_public_text(value: object) -> str:
    """Return bounded single-line text with local absolute paths removed."""

    if type(value) is not str:
        return ""
    # Collapse controls first so a newline cannot be used to split a sensitive
    # path from a suffix that should be redacted with it.
    cleaned = _CONTROL_RE.sub(" ", value)
    cleaned = _WINDOWS_PATH_RE.sub("<path>", cleaned)
    cleaned = _QUOTED_POSIX_PATH_RE.sub("<path>", cleaned)
    cleaned = _POSIX_PATH_RE.sub("<path>", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > _MAX_PUBLIC_ERROR_CHARS:
        cleaned = cleaned[: _MAX_PUBLIC_ERROR_CHARS - 3].rstrip() + "..."
    return cleaned


def raise_public_tool_error(exc: Exception) -> NoReturn:
    """Map internal failures to bounded MCP-safe messages without exception chaining."""

    if isinstance(exc, ValidationError):
        message = "DIEPI_MCP_INVALID_ARGUMENT: request parameters failed validation"
    elif isinstance(exc, ValueError):
        detail = sanitize_public_text(str(exc)) or "request parameters are invalid"
        message = f"DIEPI_MCP_INVALID_ARGUMENT: {detail}"
    elif isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
        message = "DIEPI_MCP_IO_ERROR: trusted host data could not be accessed"
    elif isinstance(exc, RuntimeError) and str(exc) == "job queue is full":
        message = "DIEPI_MCP_BUSY: the configured job queue is full"
    else:
        message = "DIEPI_MCP_INTERNAL_ERROR: operation failed"
    raise RuntimeError(message) from None


__all__ = [
    "confined_child",
    "is_run_id",
    "raise_public_tool_error",
    "require_job_id",
    "sanitize_public_text",
]
