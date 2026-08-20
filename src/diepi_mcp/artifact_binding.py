"""Bind a verified diePi RunArtifact to the adapter request that created it."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .integration import (
    TRADE_CALENDAR_SOURCE_KIND,
    TradeCalendarIdentity,
    trade_calendar_fingerprint,
)

from .models import BacktestSpec, MaCrossoverSpec
from .source_binding import source_identity_sha256
from .strategy import strategy_digest_for_template, strategy_parameters


_FIXED_PARAMETERS = {
    "freq": "daily",
    "stamp_duty": "auto",
    "transfer_fee_rate": 0.0,
    "daily_open_cap_yuan": None,
    "daily_close_cap_yuan": None,
    "limit_pct_overrides": None,
    "open_buy_resize_mode": "auto",
    "open_buy_fill_mode": "open+slip",
    "open_buy_sizing": "limit_up",
    "t0_overrides": None,
    "trading_days_per_year": 252,
}


def _mismatch(errors: list[str], condition: bool, field: str) -> None:
    if not condition and field not in errors:
        errors.append(field)


def _numeric_assumption(assumptions: Mapping[str, str], key: str, expected: Any) -> bool:
    try:
        return float(assumptions[key]) == float(expected)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def artifact_binding_errors(
    loaded: Any,
    record: Mapping[str, Any],
    request: Mapping[str, Any],
    validation_report: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return bounded public field labels for every attribution mismatch."""

    errors: list[str] = []
    try:
        strategy = MaCrossoverSpec.model_validate(request["strategy"])
        backtest = BacktestSpec.model_validate(request["backtest"])
    except Exception:
        return ("private_request.contract",)

    _mismatch(errors, loaded.manifest.run_id == record.get("run_id"), "manifest.run_id")
    _mismatch(
        errors,
        getattr(loaded.outcome.engine_kind, "value", None) == "cash_portfolio",
        "outcome.engine_kind",
    )
    _mismatch(errors, request.get("run_id") == record.get("run_id"), "request.run_id")
    _mismatch(
        errors,
        request.get("dataset_id") == record.get("dataset_id"),
        "request.dataset_id",
    )
    _mismatch(
        errors,
        request.get("request_digest") == record.get("request_digest"),
        "request.request_digest",
    )
    _mismatch(
        errors,
        request.get("strategy_digest") == record.get("strategy_digest"),
        "request.strategy_digest",
    )
    try:
        bound_strategy_digest = strategy_digest_for_template(strategy, request["template_sha256"])
    except Exception:
        bound_strategy_digest = None
    _mismatch(
        errors,
        bound_strategy_digest == record.get("strategy_digest"),
        "strategy.binding",
    )

    config = loaded.config
    _mismatch(errors, config.get("command") == "run", "config.command")
    _mismatch(
        errors,
        config.get("engine_kind") == "cash_portfolio",
        "config.engine_kind",
    )
    _mismatch(errors, config.get("input_mode") == "strategy", "config.input_mode")
    _mismatch(
        errors,
        config.get("strategy_file") == "inputs/strategy.py",
        "config.strategy_file",
    )
    _mismatch(
        errors,
        config.get("strategy_name") == "ma_crossover",
        "config.strategy_name",
    )
    _mismatch(
        errors,
        config.get("requested_start_date") == backtest.start_date,
        "config.requested_start_date",
    )
    _mismatch(
        errors,
        config.get("requested_end_date") == backtest.end_date,
        "config.requested_end_date",
    )

    parameters = config.get("parameters")
    if type(parameters) is not dict:
        return tuple((*errors, "config.parameters")[:50])
    expected_parameters = {
        **_FIXED_PARAMETERS,
        "initial_cash": backtest.initial_cash,
        "slippage": backtest.slippage,
        "commission": backtest.commission,
        "min_commission": backtest.min_commission,
        "lot_size": backtest.lot_size,
        "liquidity_cap_ratio": backtest.liquidity_cap_ratio,
        "daily_open_previous_day_ratio": backtest.daily_open_previous_day_ratio,
        "daily_close_previous_day_ratio": backtest.daily_close_previous_day_ratio,
        "risk_free_rate": backtest.risk_free_rate,
        "price_mode": request.get("resolved_price_mode"),
        "pool_symbols": [strategy.symbol],
    }
    for key, expected in expected_parameters.items():
        _mismatch(errors, parameters.get(key) == expected, f"config.parameters.{key}")

    expected_strategy_parameters = {
        **strategy_parameters(strategy),
        "DIEPI_ADAPTER_REQUEST_SHA256": record.get("request_digest"),
        "DIEPI_ADAPTER_STRATEGY_SHA256": record.get("strategy_digest"),
        "DIEPI_ADAPTER_TEMPLATE_SHA256": request.get("template_sha256"),
        "DIEPI_ADAPTER_VERSION": request.get("adapter_version"),
        "DIEPI_ADAPTER_VALIDATION_SHA256": (request.get("validation_report_sha256") or ""),
        "DIEPI_ADAPTER_SOURCES_SHA256": (request.get("validation_sources_sha256") or ""),
        "DIEPI_ADAPTER_VALIDATION_START_DATE": (request.get("validation_start_date") or ""),
    }
    _mismatch(
        errors,
        parameters.get("strategy_params") == expected_strategy_parameters,
        "config.parameters.strategy_params",
    )

    try:
        strategy_source_sha256 = hashlib.sha256(loaded.read_bytes("strategy_source")).hexdigest()
    except Exception:
        strategy_source_sha256 = None
    _mismatch(
        errors,
        strategy_source_sha256 == request.get("template_sha256"),
        "artifact.strategy_source",
    )

    result_status = loaded.outcome.result_contract.status.value
    canonical_result = loaded.result
    if result_status in {"SUCCESS", "CANCELED"}:
        _mismatch(errors, canonical_result is not None, "result")
        if canonical_result is not None:
            _mismatch(
                errors,
                getattr(canonical_result, "start_date", None) == backtest.start_date,
                "result.start_date",
            )
            _mismatch(
                errors,
                getattr(canonical_result, "end_date", None) == backtest.end_date,
                "result.end_date",
            )
            _mismatch(
                errors,
                getattr(canonical_result, "initial_cash", None) == backtest.initial_cash,
                "result.initial_cash",
            )
        _mismatch(
            errors,
            config.get("realized_symbols") == [strategy.symbol],
            "config.realized_symbols",
        )
        _mismatch(
            errors,
            set(config)
            == {
                "command",
                "engine_kind",
                "input_mode",
                "parameters",
                "realized_symbols",
                "requested_end_date",
                "requested_start_date",
                "strategy_file",
                "strategy_name",
            },
            "config.fields",
        )
        _mismatch(
            errors,
            set(parameters) == {*expected_parameters, "strategy_params"},
            "config.parameters.fields",
        )

    expected_sources = request.get("validation_sources_sha256")
    try:
        observed_sources = source_identity_sha256(loaded.provenance.sources)
    except Exception:
        observed_sources = None
    _mismatch(
        errors,
        expected_sources is not None and observed_sources == expected_sources,
        "provenance.sources",
    )

    assumptions = {item.key: item.value for item in loaded.outcome.result_contract.assumptions}
    scope = validation_report.get("scope") if isinstance(validation_report, Mapping) else None
    if not isinstance(scope, Mapping):
        _mismatch(errors, False, "validation_report.scope")
    else:
        for key, expected in {
            "symbols": [strategy.symbol],
            "start_date": request.get("validation_start_date"),
            "end_date": backtest.end_date,
            "frequency": "daily",
            "price_mode": request.get("resolved_price_mode"),
        }.items():
            _mismatch(
                errors,
                expected is not None and scope.get(key) == expected,
                f"validation_report.scope.{key}",
            )
    calendar = validation_report.get("calendar") if isinstance(validation_report, Mapping) else None
    if not isinstance(calendar, Mapping):
        _mismatch(errors, False, "validation_report.calendar")
    else:
        calendar_fields = {
            "calendar.source": calendar.get("source"),
            "calendar.id": calendar.get("calendar_id"),
            "calendar.version": calendar.get("version"),
            "calendar.content_sha256": calendar.get("content_sha256"),
            "calendar.coverage_start": calendar.get("coverage_start"),
            "calendar.coverage_end": calendar.get("coverage_end"),
            "calendar.rows": calendar.get("rows"),
            "calendar.open_days": calendar.get("open_days"),
        }
        for key, expected in calendar_fields.items():
            _mismatch(
                errors,
                expected is not None and assumptions.get(key) == str(expected),
                f"result_contract.assumptions.{key}",
            )
        try:
            calendar_identity = TradeCalendarIdentity(
                source=calendar["source"],
                calendar_id=calendar["calendar_id"],
                version=calendar["version"],
                content_sha256=calendar["content_sha256"],
                coverage_start=calendar["coverage_start"],
                coverage_end=calendar["coverage_end"],
                rows=calendar["rows"],
                open_days=calendar["open_days"],
            )
            expected_calendar_source = trade_calendar_fingerprint(calendar_identity).to_dict()
            observed_calendar_sources = [
                source.to_dict()
                for source in loaded.provenance.sources
                if source.kind == TRADE_CALENDAR_SOURCE_KIND
            ]
        except Exception:
            expected_calendar_source = None
            observed_calendar_sources = []
        _mismatch(
            errors,
            expected_calendar_source is not None
            and observed_calendar_sources == [expected_calendar_source],
            "provenance.trade_calendar",
        )
    strategy_price_mode = (
        "hfq"
        if request.get("resolved_price_mode") == "dual"
        else request.get("resolved_price_mode")
    )
    execution_price_mode = (
        "raw"
        if request.get("resolved_price_mode") == "dual"
        else request.get("resolved_price_mode")
    )
    for key, expected in {
        "execution.commission_rate": backtest.commission,
        "execution.slippage_rate": backtest.slippage,
        "execution.min_commission": backtest.min_commission,
        "execution.lot_size": backtest.lot_size,
        "execution.liquidity_cap_ratio": backtest.liquidity_cap_ratio,
        "execution.transfer_fee_rate": 0.0,
        "metrics.risk_free_rate": backtest.risk_free_rate,
        "metrics.trading_days_per_year": 252,
    }.items():
        _mismatch(
            errors,
            _numeric_assumption(assumptions, key, expected),
            f"result_contract.assumptions.{key}",
        )
    for key, expected in {
        "execution.frequency": "daily",
        "execution.stamp_duty_policy": "auto",
        "execution.strategy_price_mode": strategy_price_mode,
        "execution.execution_price_mode": execution_price_mode,
    }.items():
        _mismatch(
            errors,
            assumptions.get(key) == expected,
            f"result_contract.assumptions.{key}",
        )
    for key, expected in {
        "liquidity.daily_open_cap": backtest.daily_open_previous_day_ratio,
        "liquidity.daily_close_cap": backtest.daily_close_previous_day_ratio,
    }.items():
        value = assumptions.get(key, "")
        prefix = "previous_day_ratio:"
        _mismatch(
            errors,
            value.startswith(prefix)
            and _numeric_assumption({key: value[len(prefix) :]}, key, expected),
            f"result_contract.assumptions.{key}",
        )
    return tuple(errors[:50])


__all__ = ["artifact_binding_errors"]
