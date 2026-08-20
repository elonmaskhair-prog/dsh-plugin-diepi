"""Bounded, path-private RunArtifact verification responses."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
from typing import Any, Mapping

from .artifact_binding import artifact_binding_errors
from .config import AdapterConfig
from .integration import ArtifactStore
from .jobs import JobManager, JobStore, load_private_request, load_validation_report
from .security import confined_child, require_job_id, sanitize_public_text


_CANONICAL_METRICS = (
    "initial_cash",
    "final_value",
    "total_return",
    "annual_return",
    "sharpe_ratio",
    "max_drawdown",
    "max_drawdown_close_nav",
    "max_drawdown_intraday_low_nav",
    "max_drawdown_intraday_high_to_low",
    "trade_count",
    "win_rate",
)
_PUBLIC_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_PUBLIC_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _finite_scalar(value: Any) -> Any:
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    return None


def _scalars(value: Any, *, limit: int = 50) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result = {}
    keys = sorted(
        key for key in value if type(key) is str and _PUBLIC_KEY_RE.fullmatch(key)
    )[:limit]
    for key in keys:
        item = value[key]
        scalar = _finite_scalar(item)
        if scalar is not None or item is None:
            result[key] = scalar
        elif type(item) is str:
            result[key] = sanitize_public_text(item)
    return result


def _public_code(value: Any) -> str | None:
    return value if type(value) is str and _PUBLIC_CODE_RE.fullmatch(value) else None


def _public_result_contract(value: Any) -> dict[str, Any]:
    """Return a bounded, path-private projection of diePi's verified contract."""

    if type(value) is not dict:
        raise ValueError("verified result contract must be an object")
    status = value.get("status")
    if status not in {"SUCCESS", "PARTIAL", "INVALID", "FAILED", "CANCELED"}:
        raise ValueError("verified result contract has an invalid status")

    reason = None
    raw_reason = value.get("reason")
    if type(raw_reason) is dict:
        reason = {
            "code": _public_code(raw_reason.get("code")) or "RESULT_REASON",
            "message": sanitize_public_text(raw_reason.get("message")),
        }

    warnings = []
    raw_warnings = value.get("warnings")
    if type(raw_warnings) is list:
        for item in raw_warnings[:50]:
            if type(item) is not dict:
                continue
            warnings.append(
                {
                    "code": _public_code(item.get("code")) or "RESULT_WARNING",
                    "message": sanitize_public_text(item.get("message")),
                }
            )

    assumptions = []
    raw_assumptions = value.get("assumptions")
    if type(raw_assumptions) is list:
        for item in raw_assumptions[:50]:
            if type(item) is not dict:
                continue
            key = item.get("key")
            assumptions.append(
                {
                    "key": (
                        key
                        if type(key) is str and _PUBLIC_KEY_RE.fullmatch(key)
                        else "unknown"
                    ),
                    "value": sanitize_public_text(item.get("value")),
                }
            )

    interval = None
    raw_interval = value.get("actual_interval")
    if type(raw_interval) is dict:
        start_date = raw_interval.get("start_date")
        end_date = raw_interval.get("end_date")
        interval = {
            "start_date": start_date if type(start_date) is str and _DATE_RE.fullmatch(start_date) else None,
            "end_date": end_date if type(end_date) is str and _DATE_RE.fullmatch(end_date) else None,
        }

    coverage = None
    raw_coverage = value.get("data_coverage")
    if type(raw_coverage) is dict:
        coverage = {
            key: _finite_scalar(raw_coverage.get(key))
            for key in (
                "expected_observations",
                "actual_observations",
                "missing_observations",
                "ratio",
            )
        }

    return {
        "schema_version": value.get("schema_version") if value.get("schema_version") == 1 else None,
        "semantics_version": (
            sanitize_public_text(value.get("semantics_version"))
            if type(value.get("semantics_version")) is str
            else None
        ),
        "status": status,
        "rankable": value.get("rankable") is True,
        "reason": reason,
        "warnings": warnings,
        "assumptions": assumptions,
        "actual_interval": interval,
        "data_coverage": coverage,
    }


def _canonical_metrics(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    metrics: dict[str, Any] = {}
    for name in _CANONICAL_METRICS:
        if not hasattr(result, name):
            continue
        raw = getattr(result, name)
        value = _finite_scalar(raw)
        if value is not None or raw is None:
            metrics["final_asset" if name == "final_value" else name] = value
    return metrics


def verified_result(config: AdapterConfig, store: JobStore, job_id: str) -> dict[str, Any]:
    canonical = require_job_id(job_id)
    record = store.get(canonical)
    try:
        JobManager.public_status(record)
        dataset = config.dataset(record["dataset_id"])
    except (RuntimeError, ValueError):
        return {
            "job_id": canonical,
            "state": None,
            "artifact_available": False,
            "artifact_verified": False,
            "adapter_attribution_verified": False,
            "rankable": False,
            "verification_error": {"code": "ADAPTER_STATE_INVALID"},
        }
    try:
        run_root = confined_child(dataset.results_root, record["run_id"], "artifact directory")
    except ValueError:
        return {
            "job_id": canonical,
            "state": record.get("state"),
            "artifact_available": False,
            "artifact_verified": False,
            "adapter_attribution_verified": False,
            "rankable": False,
            "verification_error": {"code": "ADAPTER_STATE_INVALID"},
        }
    if not run_root.is_dir():
        return {
            "job_id": job_id,
            "run_id": record["run_id"],
            "state": record["state"],
            "artifact_available": False,
            "artifact_verified": False,
            "adapter_attribution_verified": False,
            "rankable": False,
        }
    try:
        loaded = ArtifactStore.load(run_root)
    except Exception as exc:
        return {
            "job_id": job_id,
            "run_id": record["run_id"],
            "state": record["state"],
            "artifact_available": True,
            "artifact_verified": False,
            "adapter_attribution_verified": False,
            "rankable": False,
            "verification_error": {
                "code": "ARTIFACT_VERIFICATION_FAILED",
                "type": sanitize_public_text(type(exc).__name__),
            },
        }

    manifest_sha256 = hashlib.sha256(loaded.manifest.to_json_bytes()).hexdigest()

    binding_errors: list[str] = []
    request: dict[str, Any] | None = None
    validation_report: dict[str, Any] | None = None
    try:
        job_dir = confined_child(
            config.state_root / "jobs", record["job_id"], "job evidence directory"
        )
    except ValueError:
        job_dir = config.state_root / ".invalid-job-evidence"
        binding_errors.append("private_request")
    try:
        request = load_private_request(job_dir / "request.json")
    except Exception:
        binding_errors.append("private_request")
    if request is not None:
        expected_validation_sha256 = request.get("validation_report_sha256")
        if expected_validation_sha256 is None:
            binding_errors.append("validation_report")
        else:
            try:
                validation_report = load_validation_report(
                    job_dir / "validation-report.json", expected_validation_sha256
                )
            except Exception:
                binding_errors.append("validation_report")
        if Path(request["data_root"]).resolve() != dataset.data_root:
            binding_errors.append("request.data_root")
        if Path(request["results_root"]).resolve() != dataset.results_root:
            binding_errors.append("request.results_root")
        binding_errors.extend(artifact_binding_errors(loaded, record, request, validation_report))
    if record.get("artifact_manifest_sha256") != manifest_sha256:
        binding_errors.append("job.artifact_manifest_sha256")
    binding_errors = list(dict.fromkeys(binding_errors))[:50]
    attribution_verified = not binding_errors

    contract = _public_result_contract(loaded.outcome.result_contract.to_dict())
    canonical_result = loaded.result
    error = loaded.outcome.error
    diagnostic = None
    if error is not None:
        diagnostic = {
            "code": _public_code(error.code) or "RESULT_ERROR",
            "category": sanitize_public_text(error.category.value),
            "phase": sanitize_public_text(error.phase),
            "exception_type": sanitize_public_text(error.exception_type),
        }
    result_status = contract.get("status")
    expected_commit = {
        "SUCCESS": ("succeeded", 0),
        "CANCELED": ("canceled", 130),
        "FAILED": ("failed", 3),
        "PARTIAL": ("failed", 3),
        "INVALID": ("failed", 3),
    }.get(result_status)
    result_committed = bool(
        expected_commit is not None
        and record["state"] == expected_commit[0]
        and record.get("terminal_status") == result_status
        and record.get("exit_code") == expected_commit[1]
        and record.get("artifact_manifest_sha256") == manifest_sha256
    )
    evidence_releasable = attribution_verified and result_committed
    metrics = _canonical_metrics(canonical_result) if evidence_releasable else {}
    execution_stats = (
        _scalars(getattr(canonical_result, "execution_stats", None)) if evidence_releasable else {}
    )
    response = {
        "job_id": job_id,
        "run_id": record["run_id"],
        "state": record["state"],
        "request_digest": record["request_digest"],
        "strategy_digest": record["strategy_digest"],
        "artifact_available": True,
        "artifact_verified": loaded.artifact_verified,
        "adapter_attribution_verified": attribution_verified,
        "result_committed": result_committed,
        "manifest_sha256": manifest_sha256,
        "committed_manifest_sha256": record.get("artifact_manifest_sha256"),
        "rankable": bool(
            loaded.is_rankable
            and attribution_verified
            and result_committed
            and record["state"] == "succeeded"
        ),
        "metrics": metrics,
        "metrics_source": (
            "verified_canonical_result"
            if evidence_releasable
            else "withheld_uncommitted_or_unattributed"
        ),
        "execution_stats": execution_stats,
    }
    if evidence_releasable:
        response.update(
            {
                "result_status": result_status,
                "result_contract": contract,
                "diagnostic": diagnostic,
                "producer": {
                    "diepi_version": sanitize_public_text(
                        loaded.manifest.producer.diepi_version
                    ),
                },
            }
        )
    else:
        # An internally valid artifact is still untrusted for this job until
        # attribution and the durable terminal commit both succeed. Keep the
        # response shape stable without releasing attacker-controlled contract,
        # warning, diagnostic, or producer text to the model.
        response.update(
            {
                "result_status": None,
                "result_contract": None,
                "diagnostic": None,
                "producer": None,
            }
        )
    if binding_errors:
        response["attribution_error"] = {
            "code": "ARTIFACT_REQUEST_BINDING_FAILED",
            "fields": binding_errors,
        }
    elif not result_committed:
        response["verification_error"] = {"code": "ARTIFACT_NOT_COMMITTED_FOR_JOB"}
    elif request is not None:
        response["execution_request"] = {
            "dataset_id": request["dataset_id"],
            "strategy": request["strategy"],
            "backtest": request["backtest"],
            "resolved_price_mode": request["resolved_price_mode"],
            "validation_report_sha256": request["validation_report_sha256"],
            "validation_sources_sha256": request["validation_sources_sha256"],
            "validation_start_date": request["validation_start_date"],
        }
    return response


__all__ = ["verified_result"]
