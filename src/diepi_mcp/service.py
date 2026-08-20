"""Domain service used by the MCP transport."""

from __future__ import annotations

from datetime import datetime
import hashlib
import inspect
import re
from typing import Any, Sequence

import diepi

from . import __version__
from .config import AdapterConfig, DatasetConfig
from .integration import (
    DataProvider,
    require_diepi_integration,
    run_backtest,
    run_doctor,
    validate_local_data,
)
from .jobs import JobManager
from .models import BacktestSpec, MaCrossoverSpec
from .results import verified_result
from .security import sanitize_public_text
from .source_binding import collect_source_identity
from .strategy import canonical_json, required_history_bars, strategy_card


_SYMBOL_RE = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")
_SUBMISSION_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{8,64}$")
_PUBLIC_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class _WarmupHistoryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_stop_check_bridge() -> None:
    try:
        parameter = inspect.signature(run_backtest).parameters.get("stop_check")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "installed diePi cannot prove run_backtest(stop_check=...) support"
        ) from exc
    if parameter is None or parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
        raise RuntimeError(
            "installed diePi must expose run_backtest(stop_check=...) for safe cancellation"
        )


def _provider_price_modes(selected: str) -> tuple[str, str]:
    if selected == "dual":
        return "hfq", "raw"
    return selected, selected


def _warmup_snapshot(
    provider: DataProvider,
    strategy: MaCrossoverSpec,
    *,
    warmup_end: str,
    required: int,
) -> tuple[str, int]:
    try:
        aligned = provider.get_aligned_pair(
            strategy.symbol,
            frequency="daily",
            end=warmup_end,
            count=required,
        )
    except Exception as exc:
        raise _WarmupHistoryError(
            "WARMUP_HISTORY_UNAVAILABLE",
            "the strategy warm-up market-data pair cannot be read",
        ) from exc
    observed = len(aligned.strategy)
    if observed == 0:
        raise _WarmupHistoryError(
            "WARMUP_HISTORY_UNAVAILABLE",
            "the strategy has no completed pre-start market-data bars",
        )
    if observed < required:
        raise _WarmupHistoryError(
            "WARMUP_HISTORY_INSUFFICIENT",
            f"strategy requires {required} completed pre-start bars but only "
            f"{observed} are available",
        )
    oldest = aligned.strategy.index[0]
    if hasattr(oldest, "strftime"):
        validation_start = oldest.strftime("%Y%m%d")
    else:
        validation_start = str(oldest).replace("-", "")[:8]
    if not re.fullmatch(r"[0-9]{8}", validation_start):
        raise _WarmupHistoryError(
            "WARMUP_HISTORY_UNAVAILABLE",
            "the strategy warm-up bars do not expose canonical trade dates",
        )
    return validation_start, observed


def _warmup_plan(
    dataset: DatasetConfig,
    strategy: MaCrossoverSpec,
    backtest: BacktestSpec,
    selected_price_mode: str,
) -> tuple[DataProvider, dict[str, Any]]:
    """Resolve the exact pre-start market-data interval read by StrategySpec v1."""

    strategy_mode, execution_mode = _provider_price_modes(selected_price_mode)
    provider = DataProvider(
        data_root=dataset.data_root,
        price_mode=strategy_mode,
        execution_price_mode=execution_mode,
    )
    required = required_history_bars(strategy)
    try:
        trade_days = provider.get_trade_days_between(backtest.start_date, backtest.end_date)
    except Exception as exc:
        raise _WarmupHistoryError(
            "WARMUP_CALENDAR_UNAVAILABLE",
            "the configured trade calendar cannot resolve the requested interval",
        ) from exc
    if not trade_days:
        raise _WarmupHistoryError(
            "BACKTEST_WINDOW_HAS_NO_TRADE_DAYS",
            "the requested interval contains no configured trade day",
        )

    first_trade_day = trade_days[0]
    try:
        warmup_end = provider.get_prev_trade_day(first_trade_day, 1)
    except Exception as exc:
        raise _WarmupHistoryError(
            "WARMUP_CALENDAR_UNAVAILABLE",
            "the configured trade calendar cannot resolve the strategy warm-up interval",
        ) from exc
    if warmup_end is None:
        raise _WarmupHistoryError(
            "WARMUP_CALENDAR_INSUFFICIENT",
            "the trade calendar has insufficient pre-start history for this strategy",
        )
    validation_start, observed = _warmup_snapshot(
        provider,
        strategy,
        warmup_end=warmup_end,
        required=required,
    )
    return provider, {
        "requested_start_date": backtest.start_date,
        "validation_start_date": validation_start,
        "validation_end_date": backtest.end_date,
        "first_backtest_trade_date": first_trade_day,
        "warmup_end_date": warmup_end,
        "required_warmup_bars": required,
        "observed_warmup_bars": observed,
    }


def _validation_binding(
    *,
    dataset_id: str,
    request_digest: str,
    selected_price_mode: str,
    validation_report_sha256: str,
    validation_sources_sha256: str,
    validation_scope: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "request_digest": request_digest,
        "selected_price_mode": selected_price_mode,
        "validation_report_sha256": validation_report_sha256,
        "validation_sources_sha256": validation_sources_sha256,
        "validation_scope": validation_scope,
    }
    return {
        **payload,
        "binding_sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
    }


def _symbols(values: Sequence[str]) -> tuple[str, ...]:
    if not values or len(values) > 32:
        raise ValueError("symbols must contain between 1 and 32 items")
    normalized = tuple(str(value).upper() for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError("symbols must not contain duplicates")
    invalid = [value for value in normalized if not _SYMBOL_RE.fullmatch(value)]
    if invalid:
        raise ValueError("symbols must use canonical codes such as 510300.SH")
    return normalized


def _price_mode(dataset: DatasetConfig, requested: str | None) -> str:
    selected = requested or dataset.default_price_mode
    allowed = {
        "dual": {"dual", "raw", "hfq"},
        "raw_only": {"raw"},
        "adjusted_only": {"hfq"},
    }[dataset.data_grade]
    if selected not in allowed:
        raise ValueError(
            f"dataset {dataset.dataset_id} is declared {dataset.data_grade}; "
            f"allowed price_mode values are {sorted(allowed)}"
        )
    return selected


def _public_validation_projection(report: Any) -> dict[str, Any]:
    """Project host validation evidence without messages, samples, or local paths."""

    raw = report.to_dict()
    if type(raw) is not dict:
        raise RuntimeError("diePi validation report is invalid")

    scope_raw = raw.get("scope") if type(raw.get("scope")) is dict else {}
    symbols = scope_raw.get("symbols") if type(scope_raw.get("symbols")) is list else []
    scope = {
        "symbols": [value for value in symbols[:32] if type(value) is str and _SYMBOL_RE.fullmatch(value)],
        "start_date": (
            scope_raw.get("start_date")
            if type(scope_raw.get("start_date")) is str
            and re.fullmatch(r"[0-9]{8}", scope_raw["start_date"])
            else None
        ),
        "end_date": (
            scope_raw.get("end_date")
            if type(scope_raw.get("end_date")) is str
            and re.fullmatch(r"[0-9]{8}", scope_raw["end_date"])
            else None
        ),
        "frequency": scope_raw.get("frequency") if scope_raw.get("frequency") == "daily" else None,
        "price_mode": (
            scope_raw.get("price_mode")
            if scope_raw.get("price_mode") in {"dual", "raw", "hfq"}
            else None
        ),
    }

    calendar_raw = raw.get("calendar") if type(raw.get("calendar")) is dict else {}
    calendar: dict[str, Any] = {}
    for key in (
        "status",
        "source",
        "calendar_id",
        "version",
        "content_sha256",
        "first_date",
        "last_date",
    ):
        value = calendar_raw.get(key)
        if value is None:
            calendar[key] = None
        elif key == "content_sha256":
            calendar[key] = value if type(value) is str and _DIGEST_RE.fullmatch(value) else None
        elif key in {"first_date", "last_date"}:
            calendar[key] = (
                value if type(value) is str and re.fullmatch(r"[0-9]{8}", value) else None
            )
        else:
            calendar[key] = sanitize_public_text(value)
    for key in ("rows", "open_days_in_scope"):
        value = calendar_raw.get(key)
        calendar[key] = value if type(value) is int and value >= 0 else None

    issues = []
    issues_raw = raw.get("issues") if type(raw.get("issues")) is list else []
    for item in issues_raw[:50]:
        if type(item) is not dict:
            continue
        code = item.get("code")
        severity = item.get("severity")
        symbol = item.get("symbol")
        issues.append(
            {
                "code": code if type(code) is str and _PUBLIC_CODE_RE.fullmatch(code) else "VALIDATION_ISSUE",
                "severity": severity if severity in {"error", "warning", "info"} else "error",
                "symbol": (
                    symbol if type(symbol) is str and _SYMBOL_RE.fullmatch(symbol) else None
                ),
            }
        )

    pairs = []
    pairs_raw = raw.get("pair_reports") if type(raw.get("pair_reports")) is list else []
    for item in pairs_raw[:32]:
        if type(item) is not dict:
            continue
        pair_issues = item.get("issues") if type(item.get("issues")) is list else []
        issue_codes = []
        for issue in pair_issues[:50]:
            code = issue.get("code") if type(issue) is dict else None
            if type(code) is str and _PUBLIC_CODE_RE.fullmatch(code):
                issue_codes.append(code)
        pairs.append(
            {
                "status": item.get("status") if item.get("status") in {"pass", "fail"} else "fail",
                "symbol": (
                    item.get("symbol")
                    if type(item.get("symbol")) is str and _SYMBOL_RE.fullmatch(item["symbol"])
                    else None
                ),
                "strategy_rows": (
                    item.get("strategy_rows")
                    if type(item.get("strategy_rows")) is int and item["strategy_rows"] >= 0
                    else None
                ),
                "execution_rows": (
                    item.get("execution_rows")
                    if type(item.get("execution_rows")) is int and item["execution_rows"] >= 0
                    else None
                ),
                "aligned_rows": (
                    item.get("aligned_rows")
                    if type(item.get("aligned_rows")) is int and item["aligned_rows"] >= 0
                    else None
                ),
                "issue_codes": issue_codes,
            }
        )

    contract_ready = raw.get("contract_ready") is True
    return {
        "schema_version": 1,
        "projection": "diepi_mcp.validation_public_v1",
        "status": "pass" if contract_ready else "fail",
        "contract_ready": contract_ready,
        "scope": scope,
        "dataset_kind": (
            raw.get("dataset_kind")
            if raw.get("dataset_kind")
            in {"synthetic_demo", "user_supplied", "user_supplied_unmanifested"}
            else "unknown"
        ),
        "manifest_status": (
            raw.get("manifest_status")
            if raw.get("manifest_status") in {"absent", "verified", "failed", "not_checked"}
            else "unknown"
        ),
        "manifest_verified": raw.get("manifest_status") == "verified",
        "calendar": calendar,
        "pair_reports": pairs,
        "issues": issues,
        "issue_count": len(issues_raw),
    }


class QuantService:
    def __init__(self, config: AdapterConfig):
        require_diepi_integration()
        _require_stop_check_bridge()
        self.config = config
        self.jobs = JobManager(config)

    def close(self) -> None:
        """Release worker and state-root ownership held by this service."""

        self.jobs.close()

    def capabilities(self) -> dict[str, Any]:
        datasets = []
        for key in sorted(self.config.datasets):
            projected = self.config.datasets[key].public_dict()
            projected["description"] = sanitize_public_text(projected["description"])
            datasets.append(projected)
        return {
            "schema_version": 1,
            "adapter_version": __version__,
            "diepi_version": diepi.__version__,
            "mode": "research_backtest_only",
            "datasets": datasets,
            "strategy_specs": [
                {
                    "schema_version": 1,
                    "strategy_type": "ma_crossover",
                    "frequency": "daily",
                    "asset_scope": "one A-share stock or ETF",
                    "optional_filters": ["amount_expansion"],
                }
            ],
            "supported_price_modes": ["dual", "raw", "hfq"],
            "graceful_cancel_supported": True,
            "data_contract": {
                "layout": "diepi.market_data_v1",
                "frequency": "daily",
                "required_market_fields": [
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "amount",
                ],
                "required_context": [
                    "canonical instrument metadata",
                    "trading calendar",
                    "declared price-adjustment semantics",
                    "adjustment factors for dual mode",
                ],
                "daily_amount_unit": "thousand_yuan",
                "admission": (
                    "exact-scope validation over the requested interval and every completed "
                    "pre-start bar visible to the strategy; a manifest is verified when "
                    "present or required by the dataset contract"
                ),
            },
            "execution_model": {
                "granularity": "bar_based_cash_account_not_order_book",
                "timeline": [
                    "before_open(T) observes only completed strategy-price bars through T-1",
                    "target-percent DAY orders created before_open(T) are first eligible at T open",
                    "unfilled DAY remainder is canceled at day end",
                ],
                "price_lanes": (
                    "dual uses adjusted prices for signals and strictly aligned raw prices for "
                    "matching; raw/hfq single-lane modes use that lane for both"
                ),
                "open_orders": (
                    "directional slippage is applied to the execution-lane open; automatic "
                    "cash resizing sizes buys conservatively at the limit-up price"
                ),
                "market_rules": (
                    "instrument/date-aware ticks, lot rules, price limits, T+0/T+1 settlement, "
                    "cash and available-position checks"
                ),
                "fees": (
                    "commission rate plus minimum commission, automatic instrument/date-aware "
                    "stamp duty, and zero transfer fee in StrategySpec v1"
                ),
                "liquidity": (
                    "open and close auctions each use an explicit previous-day amount ratio; "
                    "bar liquidity is a capacity cap, not a market-impact model"
                ),
                "strategy_order_policy": (
                    "orders are submitted only on a strict crossover event; a rejected order is "
                    "not retried merely because the MA regime persists"
                ),
                "end_of_test": "remaining holdings are marked to market; no forced liquidation",
                "run_specific_authority": (
                    "the verified ResultContract assumptions and execution statistics"
                ),
            },
            "explicitly_unavailable": [
                "arbitrary_python",
                "live_trading",
                "futures",
                "short_selling",
                "leverage",
                "minute_strategy_specs",
            ],
        }

    def doctor(self, dataset_id: str) -> dict[str, Any]:
        dataset = self.config.dataset(dataset_id)
        report = run_doctor(
            data_root=dataset.data_root,
            results_root=dataset.results_root,
            check_gui=False,
        ).to_dict()
        checks = []
        for check in report["checks"]:
            public = {
                "name": sanitize_public_text(check["name"]),
                "status": sanitize_public_text(check["status"]),
                "message": sanitize_public_text(check["message"]),
            }
            if not check["name"].endswith("root"):
                value = check.get("value")
                public["value"] = sanitize_public_text(value) if isinstance(value, str) else value
            checks.append(public)
        return {
            "schema_version": report["schema_version"],
            "status": report["status"],
            "ok": report["ok"],
            "diepi_version": report["diepi_version"],
            "dataset_id": dataset_id,
            "data_grade": dataset.data_grade,
            "default_price_mode": dataset.default_price_mode,
            "checks": checks,
        }

    def validate_data(
        self,
        dataset_id: str,
        symbols: Sequence[str],
        start_date: str,
        end_date: str,
        price_mode: str | None = None,
    ) -> dict[str, Any]:
        dataset = self.config.dataset(dataset_id)
        normalized = _symbols(symbols)
        # Reuse the versioned request model for strict date validation.
        dates = BacktestSpec(start_date=start_date, end_date=end_date)
        self._check_interval(dates)
        selected = _price_mode(dataset, price_mode)
        report = validate_local_data(
            data_root=dataset.data_root,
            symbols=normalized,
            start_date=dates.start_date,
            end_date=dates.end_date,
            frequency="daily",
            price_mode=selected,
            verify_manifest=True,
        )
        return {
            "dataset_id": dataset_id,
            "data_grade": dataset.data_grade,
            "selected_price_mode": selected,
            **_public_validation_projection(report),
        }

    @staticmethod
    def preview_strategy(strategy: MaCrossoverSpec) -> dict[str, Any]:
        return strategy_card(strategy)

    def start_backtest(
        self,
        dataset_id: str,
        strategy: MaCrossoverSpec,
        backtest: BacktestSpec,
        submission_id: str,
    ) -> dict[str, Any]:
        if not _SUBMISSION_ID_RE.fullmatch(submission_id):
            raise ValueError("submission_id must match req_[A-Za-z0-9_-]{8,64}")
        dataset = self.config.dataset(dataset_id)
        self._check_interval(backtest)
        selected = _price_mode(dataset, backtest.price_mode)
        try:
            _, sources_before_validation = collect_source_identity(
                dataset.data_root,
                symbol=strategy.symbol,
                price_mode=selected,
                start_date=backtest.start_date,
                end_date=backtest.end_date,
            )
        except Exception:
            return {
                "accepted": False,
                "code": "DATA_SOURCE_IDENTITY_UNAVAILABLE",
                "dataset_id": dataset_id,
                "selected_price_mode": selected,
                "message": "market-data source files cannot be fingerprinted",
            }
        try:
            _, validation_scope = _warmup_plan(dataset, strategy, backtest, selected)
        except _WarmupHistoryError as exc:
            return {
                "accepted": False,
                "code": exc.code,
                "dataset_id": dataset_id,
                "selected_price_mode": selected,
                "message": str(exc),
            }
        validation = validate_local_data(
            data_root=dataset.data_root,
            symbols=(strategy.symbol,),
            start_date=validation_scope["validation_start_date"],
            end_date=backtest.end_date,
            frequency="daily",
            price_mode=selected,
            verify_manifest=True,
        )
        if not validation.contract_ready:
            return {
                "accepted": False,
                "code": "DATA_CONTRACT_NOT_READY",
                "dataset_id": dataset_id,
                "selected_price_mode": selected,
                "validation_scope": validation_scope,
                "validation": _public_validation_projection(validation),
            }
        try:
            _, confirmed_scope = _warmup_plan(dataset, strategy, backtest, selected)
        except _WarmupHistoryError as exc:
            return {
                "accepted": False,
                "code": exc.code,
                "dataset_id": dataset_id,
                "selected_price_mode": selected,
                "validation_scope": validation_scope,
                "validation_report_sha256": validation.report_sha256,
                "message": str(exc),
            }
        if confirmed_scope != validation_scope:
            return {
                "accepted": False,
                "code": "WARMUP_DATA_CHANGED",
                "dataset_id": dataset_id,
                "selected_price_mode": selected,
                "validation_scope": validation_scope,
                "validation_report_sha256": validation.report_sha256,
                "message": "strategy warm-up data changed during validation",
            }
        try:
            _, validation_sources_sha256 = collect_source_identity(
                dataset.data_root,
                symbol=strategy.symbol,
                price_mode=selected,
                start_date=backtest.start_date,
                end_date=backtest.end_date,
            )
        except Exception:
            return {
                "accepted": False,
                "code": "DATA_SOURCE_IDENTITY_UNAVAILABLE",
                "dataset_id": dataset_id,
                "selected_price_mode": selected,
                "validation_scope": validation_scope,
                "validation_report_sha256": validation.report_sha256,
                "message": "validated market-data source files cannot be fingerprinted",
            }
        if validation_sources_sha256 != sources_before_validation:
            return {
                "accepted": False,
                "code": "DATA_CHANGED_DURING_VALIDATION",
                "dataset_id": dataset_id,
                "selected_price_mode": selected,
                "validation_scope": validation_scope,
                "validation_report_sha256": validation.report_sha256,
                "message": "market-data source files changed during admission validation",
            }
        status = self.jobs.start(
            dataset,
            strategy,
            backtest,
            submission_id,
            validation_report_sha256=validation.report_sha256,
            validation_report=validation.to_dict(),
            validation_sources_sha256=validation_sources_sha256,
            validation_start_date=validation_scope["validation_start_date"],
        )
        binding = _validation_binding(
            dataset_id=dataset_id,
            request_digest=status["request_digest"],
            selected_price_mode=selected,
            validation_report_sha256=validation.report_sha256,
            validation_sources_sha256=validation_sources_sha256,
            validation_scope=validation_scope,
        )
        return {
            "accepted": True,
            "submission_id": submission_id,
            "selected_price_mode": selected,
            "validation_report_sha256": validation.report_sha256,
            "validation_sources_sha256": validation_sources_sha256,
            "validation_scope": validation_scope,
            "validation_binding": binding,
            **status,
        }

    def job_status(self, job_id: str) -> dict[str, Any]:
        return self.jobs.status(job_id)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.cancel(job_id)

    def get_result(self, job_id: str) -> dict[str, Any]:
        self.jobs.status(job_id)
        return verified_result(self.config, self.jobs.store, job_id)

    def _check_interval(self, backtest: BacktestSpec) -> None:
        start = datetime.strptime(backtest.start_date, "%Y%m%d")
        end = datetime.strptime(backtest.end_date, "%Y%m%d")
        if (end - start).days > self.config.max_calendar_days:
            raise ValueError("requested interval exceeds configured max_calendar_days")


__all__ = ["QuantService"]
