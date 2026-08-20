"""Trusted host configuration and opaque dataset registry."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


CONFIG_ENV = "DIEPI_MCP_CONFIG"
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_GRADES = {"dual", "raw_only", "adjusted_only"}
_PRICE_MODES = {"dual", "raw", "hfq"}
_GRADE_PRICE_MODE = {
    "dual": "dual",
    "raw_only": "raw",
    "adjusted_only": "hfq",
}


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    return value


def _known_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ValueError(f"{label} contains unknown fields: {', '.join(extra)}")


def _path(base: Path, raw: Any, label: str, *, must_exist: bool) -> Path:
    if type(raw) is not str or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path string")
    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=must_exist)
    if must_exist and not resolved.is_dir():
        raise ValueError(f"{label} must be an existing directory")
    return resolved


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class DatasetConfig:
    dataset_id: str
    description: str
    data_root: Path
    results_root: Path
    data_grade: str
    default_price_mode: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "description": self.description,
            "data_grade": self.data_grade,
            "default_price_mode": self.default_price_mode,
        }


@dataclass(frozen=True)
class AdapterConfig:
    source: Path
    state_root: Path
    max_concurrent_jobs: int
    max_queued_jobs: int
    max_calendar_days: int
    datasets: Mapping[str, DatasetConfig]

    def dataset(self, dataset_id: str) -> DatasetConfig:
        try:
            return self.datasets[dataset_id]
        except KeyError:
            raise ValueError(f"unknown dataset_id: {dataset_id}") from None


def load_config(path: str | os.PathLike[str] | None = None) -> AdapterConfig:
    selected = path or os.environ.get(CONFIG_ENV)
    if not selected:
        raise ValueError(f"configuration is required; pass --config or set {CONFIG_ENV}")
    source = Path(selected).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("configuration path must be a JSON file")
    # ``utf-8-sig`` accepts both ordinary UTF-8 and the BOM emitted by Windows
    # PowerShell 5.1 without weakening JSON decoding semantics.
    raw = _object(json.loads(source.read_text(encoding="utf-8-sig")), "configuration")
    _known_keys(
        raw,
        {
            "schema_version",
            "state_root",
            "max_concurrent_jobs",
            "max_queued_jobs",
            "max_calendar_days",
            "datasets",
        },
        "configuration",
    )
    if raw.get("schema_version") != 1:
        raise ValueError("configuration schema_version must be 1")
    base = source.parent
    state_root = _path(
        base, raw.get("state_root", ".diepi-mcp-state"), "state_root", must_exist=False
    )
    max_concurrent = raw.get("max_concurrent_jobs", 1)
    max_queued = raw.get("max_queued_jobs", 32)
    max_days = raw.get("max_calendar_days", 10_000)
    if type(max_concurrent) is not int or not 1 <= max_concurrent <= 16:
        raise ValueError("max_concurrent_jobs must be an integer in [1, 16]")
    if type(max_queued) is not int or not 1 <= max_queued <= 1_000:
        raise ValueError("max_queued_jobs must be an integer in [1, 1000]")
    if type(max_days) is not int or not 1 <= max_days <= 100_000:
        raise ValueError("max_calendar_days must be an integer in [1, 100000]")

    datasets_raw = _object(raw.get("datasets"), "datasets")
    if not datasets_raw:
        raise ValueError("datasets must contain at least one configured dataset")
    datasets: dict[str, DatasetConfig] = {}
    for dataset_id, item_raw in datasets_raw.items():
        if type(dataset_id) is not str or not _ID_RE.fullmatch(dataset_id):
            raise ValueError(f"invalid dataset id: {dataset_id!r}")
        item = _object(item_raw, f"datasets.{dataset_id}")
        _known_keys(
            item,
            {"description", "data_root", "results_root", "data_grade", "default_price_mode"},
            f"datasets.{dataset_id}",
        )
        data_root = _path(
            base, item.get("data_root"), f"datasets.{dataset_id}.data_root", must_exist=True
        )
        results_root = _path(
            base,
            item.get("results_root"),
            f"datasets.{dataset_id}.results_root",
            must_exist=False,
        )
        grade = item.get("data_grade")
        if grade not in _GRADES:
            raise ValueError(f"datasets.{dataset_id}.data_grade must be one of {sorted(_GRADES)}")
        default_price = item.get("default_price_mode", _GRADE_PRICE_MODE[grade])
        if default_price not in _PRICE_MODES:
            raise ValueError(
                f"datasets.{dataset_id}.default_price_mode must be one of {sorted(_PRICE_MODES)}"
            )
        if default_price != _GRADE_PRICE_MODE[grade]:
            raise ValueError(
                f"datasets.{dataset_id} data_grade={grade} requires "
                f"default_price_mode={_GRADE_PRICE_MODE[grade]}"
            )
        if (
            _contains(data_root, results_root)
            or _contains(results_root, data_root)
            or data_root == results_root
        ):
            raise ValueError("results_root and immutable data_root must not overlap")
        description = item.get("description", "")
        if type(description) is not str or len(description) > 500:
            raise ValueError(f"datasets.{dataset_id}.description must be at most 500 characters")
        datasets[dataset_id] = DatasetConfig(
            dataset_id=dataset_id,
            description=description,
            data_root=data_root,
            results_root=results_root,
            data_grade=grade,
            default_price_mode=default_price,
        )

    data_roots = [(item.dataset_id, item.data_root) for item in datasets.values()]
    writable_roots = [("state_root", state_root)] + [
        (f"datasets.{item.dataset_id}.results_root", item.results_root)
        for item in datasets.values()
    ]
    for writable_label, writable_root in writable_roots:
        for dataset_id, data_root in data_roots:
            if _contains(data_root, writable_root) or _contains(writable_root, data_root):
                raise ValueError(
                    f"{writable_label} and datasets.{dataset_id}.data_root must not overlap"
                )
    for index, (left_label, left_root) in enumerate(writable_roots):
        for right_label, right_root in writable_roots[index + 1 :]:
            if _contains(left_root, right_root) or _contains(right_root, left_root):
                raise ValueError(f"{left_label} and {right_label} must not overlap")
    return AdapterConfig(
        source=source,
        state_root=state_root,
        max_concurrent_jobs=max_concurrent,
        max_queued_jobs=max_queued,
        max_calendar_days=max_days,
        datasets=datasets,
    )


__all__ = ["AdapterConfig", "CONFIG_ENV", "DatasetConfig", "load_config"]
