"""Create a new adapter home backed only by diePi's deterministic synthetic demo."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any
import uuid

from .integration import (
    DEMO_END_DATE,
    DEMO_START_DATE,
    DEMO_SYMBOL,
    generate_synthetic_demo,
    require_diepi_integration,
)


CONFIG_FILENAME = "diepi-mcp.json"
_STAGING_PREFIX = ".diepi-mcp-init-"
_INCOMPLETE_FILENAME = ".diepi-mcp-incomplete"
_INCOMPLETE_TEXT = (
    "Initialization did not complete. This directory must not be used.\n"
    "Remove it manually and choose a new target directory.\n"
)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return (metadata.st_dev, metadata.st_ino)


def _safe_cleanup_staging(staging: Path, parent: Path) -> None:
    try:
        metadata = staging.lstat()
    except OSError:
        return
    if (
        staging.parent == parent
        and staging.name.startswith(_STAGING_PREFIX)
        and stat.S_ISDIR(metadata.st_mode)
        and not staging.is_symlink()
        and not _is_reparse(metadata)
    ):
        shutil.rmtree(staging)


def _require_plain_directory_chain(path: Path) -> tuple[int, int]:
    """Reject symlink/junction traversal in every existing parent component."""

    parts = path.parts
    if not parts:
        raise ValueError("target parent is invalid")
    current = Path(parts[0])
    metadata: os.stat_result | None = None
    for part in (None, *parts[1:]):
        if part is not None:
            current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise FileNotFoundError("target parent must be an existing directory") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or current.is_symlink()
            or _is_reparse(metadata)
        ):
            raise ValueError("target parent components must be plain directories")
    assert metadata is not None
    return _identity(metadata)


def _require_same_plain_directory(path: Path, expected: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError("claimed target changed during initialization") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or _is_reparse(metadata)
        or _identity(metadata) != expected
    ):
        raise RuntimeError("claimed target changed during initialization")


def _claim_destination_exclusive(destination: Path) -> tuple[int, int]:
    """Claim ``destination`` without any replace-if-empty directory semantics."""

    try:
        destination.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError:
        raise FileExistsError("target already exists; choose a new directory") from None
    try:
        metadata = destination.lstat()
    except OSError as exc:
        raise RuntimeError("claimed target changed during initialization") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or destination.is_symlink()
        or _is_reparse(metadata)
    ):
        raise RuntimeError("claimed target is not a plain directory")
    return _identity(metadata)


def _write_text_exclusive(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _install_staged_entry_no_replace(source: Path, destination: Path) -> None:
    """Install one staged tree without replacing any raced file, link, or empty dir."""

    metadata = source.lstat()
    if source.is_symlink() or _is_reparse(metadata):
        raise ValueError("staged content must not contain links or reparse points")
    if stat.S_ISDIR(metadata.st_mode):
        destination.mkdir(mode=stat.S_IMODE(metadata.st_mode), exist_ok=False)
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            _install_staged_entry_no_replace(child, destination / child.name)
        source.rmdir()
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("staged content must contain only regular files and directories")
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    source.unlink()


def _restore_incomplete_marker(destination: Path) -> None:
    marker = destination / _INCOMPLETE_FILENAME
    if os.path.lexists(marker):
        return
    try:
        _write_text_exclusive(marker, _INCOMPLETE_TEXT)
    except OSError:
        # The absence of the committed config still makes the target unusable.
        return


def initialize_synthetic_home(target: str | Path) -> dict[str, Any]:
    """Exclusively create one new, self-contained synthetic adapter home.

    The destination and its parent must be explicit. Existing destinations are
    never reused, merged, or overwritten. The config is the final commit file;
    an interrupted initialization retains an explicitly incomplete destination.
    """

    require_diepi_integration()
    expanded = Path(target).expanduser()
    destination = Path(os.path.abspath(expanded))
    parent = destination.parent
    parent_identity = _require_plain_directory_chain(parent)
    destination_identity = _claim_destination_exclusive(destination)

    staging = parent / f"{_STAGING_PREFIX}{uuid.uuid4().hex}"
    try:
        if _require_plain_directory_chain(parent) != parent_identity:
            raise RuntimeError("target parent changed during initialization")
        _require_same_plain_directory(destination, destination_identity)
        _write_text_exclusive(destination / _INCOMPLETE_FILENAME, _INCOMPLETE_TEXT)
        staging.mkdir(mode=0o700, exist_ok=False)
        demo = generate_synthetic_demo(staging / "synthetic-demo")
        (staging / "state").mkdir()
        (staging / "results").mkdir()
        config = {
            "schema_version": 1,
            "state_root": "state",
            "max_concurrent_jobs": 1,
            "max_queued_jobs": 32,
            "max_calendar_days": 10_000,
            "datasets": {
                "synthetic-demo": {
                    "description": (
                        "Locally generated deterministic synthetic daily data; not market data"
                    ),
                    "data_root": "synthetic-demo/market-data",
                    "results_root": "results",
                    "data_grade": "dual",
                    "default_price_mode": "dual",
                }
            },
        }
        config_text = (
            json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            + "\n"
        )
        (staging / "README.txt").write_text(
            "diePi MCP synthetic home\n"
            "\n"
            "All prices here were generated locally and are not market data.\n"
            "Keep this home, especially state/ and results/, outside an Agent-writable workspace.\n",
            encoding="utf-8",
        )
        if _require_plain_directory_chain(parent) != parent_identity:
            raise RuntimeError("target parent changed during initialization")
        _require_same_plain_directory(destination, destination_identity)
        for entry in sorted(staging.iterdir(), key=lambda item: item.name):
            _install_staged_entry_no_replace(entry, destination / entry.name)
        staging.rmdir()
        _require_same_plain_directory(destination, destination_identity)
        (destination / _INCOMPLETE_FILENAME).unlink()
        try:
            _write_text_exclusive(destination / CONFIG_FILENAME, config_text)
        except Exception:
            _restore_incomplete_marker(destination)
            raise
    except Exception:
        _safe_cleanup_staging(staging, parent)
        raise

    return {
        "ok": True,
        "schema_version": 1,
        "synthetic": True,
        "target": str(destination),
        "config": str(destination / CONFIG_FILENAME),
        "dataset_id": "synthetic-demo",
        "symbol": DEMO_SYMBOL,
        "available_start_date": DEMO_START_DATE,
        "available_end_date": DEMO_END_DATE,
        "data_root": str(destination / "synthetic-demo" / "market-data"),
        "state_root": str(destination / "state"),
        "results_root": str(destination / "results"),
        "manifest_sha256": demo.manifest.manifest_sha256,
        "validation_report_sha256": demo.validation_report.report_sha256,
    }


__all__ = ["CONFIG_FILENAME", "initialize_synthetic_home"]
