"""Isolated diePi worker. This module is never exposed as an arbitrary command."""

from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .integration import require_diepi_integration, run_backtest
from .jobs import load_private_request
from .models import BacktestSpec, MaCrossoverSpec
from .source_binding import collect_source_identity
from .strategy import strategy_digest, strategy_parameters, template_digest


class _DataSourceIdentityError(RuntimeError):
    pass


class _CancellationBridgeError(RuntimeError):
    pass


def _require_stop_check_bridge() -> None:
    try:
        parameter = inspect.signature(run_backtest).parameters.get("stop_check")
    except (TypeError, ValueError) as exc:
        raise _CancellationBridgeError(
            "installed diePi cannot prove run_backtest(stop_check=...) support"
        ) from exc
    if parameter is None or parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
        raise _CancellationBridgeError(
            "installed diePi must expose run_backtest(stop_check=...) for safe cancellation"
        )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_request(path: Path) -> dict[str, Any]:
    return load_private_request(path)


def execute(request_path: Path, result_path: Path, cancel_file: Path) -> int:
    try:
        request = _load_request(request_path)
        # The host process performs the same compatibility gate, but the
        # isolated worker must independently reject a substituted/older diePi.
        require_diepi_integration()
        _require_stop_check_bridge()
        strategy = MaCrossoverSpec.model_validate(request["strategy"])
        backtest = BacktestSpec.model_validate(request["backtest"])
        if request["adapter_version"] != __version__:
            raise ValueError("worker request adapter_version does not match this worker")
        if request["template_digest"] != template_digest():
            raise ValueError("worker request template digest does not match this worker")
        if request["strategy_digest"] != strategy_digest(strategy):
            raise ValueError("worker request strategy digest does not match its strategy")
        if (
            backtest.price_mode is not None
            and request["resolved_price_mode"] != backtest.price_mode
        ):
            raise ValueError("worker request resolved_price_mode conflicts with backtest")
        validation_start = request["validation_start_date"]
        if validation_start is not None and validation_start > backtest.start_date:
            raise ValueError("worker request validation_start_date is after the backtest start")
        expected_sources = request["validation_sources_sha256"]
        if expected_sources is not None:
            try:
                _, observed_sources = collect_source_identity(
                    Path(request["data_root"]),
                    symbol=strategy.symbol,
                    price_mode=request["resolved_price_mode"],
                    start_date=backtest.start_date,
                    end_date=backtest.end_date,
                )
            except Exception as exc:
                raise _DataSourceIdentityError(
                    "validated market-data source identity cannot be reproduced"
                ) from exc
            if observed_sources != expected_sources:
                raise _DataSourceIdentityError(
                    "market-data sources changed after admission validation"
                )

        strategy_file = Path(__file__).with_name("strategies") / "ma_crossover.py"
        runtime_parameters = strategy_parameters(strategy)
        runtime_parameters.update(
            {
                "DIEPI_ADAPTER_REQUEST_SHA256": request["request_digest"],
                "DIEPI_ADAPTER_STRATEGY_SHA256": request["strategy_digest"],
                "DIEPI_ADAPTER_TEMPLATE_SHA256": request["template_digest"],
                "DIEPI_ADAPTER_VERSION": request["adapter_version"],
                "DIEPI_ADAPTER_VALIDATION_SHA256": request["validation_report_sha256"] or "",
                "DIEPI_ADAPTER_SOURCES_SHA256": expected_sources or "",
                "DIEPI_ADAPTER_VALIDATION_START_DATE": validation_start or "",
            }
        )
        kwargs: dict[str, Any] = {
            "strategy_file": str(strategy_file),
            "start_date": backtest.start_date,
            "end_date": backtest.end_date,
            "initial_cash": backtest.initial_cash,
            "output_dir": Path(request["results_root"]),
            "run_name": request["run_id"],
            "freq": "daily",
            "slippage": backtest.slippage,
            "commission": backtest.commission,
            "pool_symbols": [strategy.symbol],
            "stamp_duty": "auto",
            "min_commission": backtest.min_commission,
            "lot_size": backtest.lot_size,
            "liquidity_cap_ratio": backtest.liquidity_cap_ratio,
            "daily_open_previous_day_ratio": backtest.daily_open_previous_day_ratio,
            "daily_close_previous_day_ratio": backtest.daily_close_previous_day_ratio,
            "strategy_params": runtime_parameters,
            "risk_free_rate": backtest.risk_free_rate,
            "price_mode": request["resolved_price_mode"],
            "verbose": False,
            "data_root": Path(request["data_root"]),
        }
        # ``lexists`` observes a marker without following a substituted link.
        # Any directory entry is a fail-safe request to stop.
        kwargs["stop_check"] = lambda: os.path.lexists(cancel_file)
        output = run_backtest(**kwargs)
        _atomic_json(
            result_path,
            {"schema_version": 1, "ok": True, "output": output},
        )
        contract = output.get("result_contract") or {}
        if contract.get("status") == "SUCCESS" and output.get("artifact_verified") is True:
            return 0
        if contract.get("status") == "CANCELED":
            return 130
        return 3
    except Exception as exc:
        if isinstance(exc, _CancellationBridgeError):
            code = "CANCEL_BRIDGE_UNAVAILABLE"
            exit_code = 4
        elif isinstance(exc, _DataSourceIdentityError):
            code = "DATA_SOURCE_IDENTITY_CHANGED"
            exit_code = 1
        else:
            code = "DIEPI_RUN_FAILED"
            exit_code = 1
        _atomic_json(
            result_path,
            {
                "schema_version": 1,
                "ok": False,
                "error": {
                    "code": code,
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
            },
        )
        return exit_code


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Internal diePi MCP worker")
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--cancel-file", required=True)
    args = parser.parse_args(argv)
    raise SystemExit(execute(Path(args.request), Path(args.result), Path(args.cancel_file)))


if __name__ == "__main__":
    main()
