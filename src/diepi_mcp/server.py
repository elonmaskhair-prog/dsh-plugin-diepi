"""MCP stdio server for the constrained diePi research surface."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import sys
from typing import Any, AsyncIterator

from mcp.server.fastmcp import FastMCP

from .config import CONFIG_ENV, load_config
from .models import AmountFilter, BacktestSpec, MaCrossoverSpec
from .security import raise_public_tool_error
from .service import QuantService


def _public_call(function, *args):
    try:
        return function(*args)
    except Exception as exc:
        raise_public_tool_error(exc)


async def _public_thread_call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except Exception as exc:
        raise_public_tool_error(exc)


def create_server(config_path: str | None = None) -> FastMCP:
    service = QuantService(load_config(config_path))

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await asyncio.to_thread(service.close)

    server = FastMCP(
        "diepi-quant",
        instructions=(
            "Constrained diePi research tools. Preview a typed strategy, validate "
            "the configured dataset, start an asynchronous backtest, then verify "
            "the immutable RunArtifact before comparing metrics."
        ),
        lifespan=lifespan,
    )

    @server.tool()
    def capabilities() -> dict[str, Any]:
        """List configured opaque datasets, supported StrategySpec types, and hard limits."""

        return _public_call(service.capabilities)

    @server.tool()
    async def doctor(dataset_id: str) -> dict[str, Any]:
        """Run path-private installation diagnostics for one configured dataset."""

        return await _public_thread_call(service.doctor, dataset_id)

    @server.tool()
    async def validate_data(
        dataset_id: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_mode: str = "",
    ) -> dict[str, Any]:
        """Strictly validate daily OHLC/amount data and price-lane semantics without writing."""

        return await _public_thread_call(
            service.validate_data,
            dataset_id,
            symbols,
            start_date,
            end_date,
            price_mode or None,
        )

    @server.tool()
    def preview_strategy(
        symbol: str,
        schema_version: int = 1,
        strategy_type: str = "ma_crossover",
        fast_window: int = 5,
        slow_window: int = 20,
        target_weight: float = 0.95,
        amount_lookback: int = 0,
        amount_minimum_ratio: float = 1.0,
    ) -> dict[str, Any]:
        """Validate StrategySpec v1 and return its exact causal/execution strategy card."""

        try:
            strategy = _strategy_from_flat_args(
                symbol=symbol,
                schema_version=schema_version,
                strategy_type=strategy_type,
                fast_window=fast_window,
                slow_window=slow_window,
                target_weight=target_weight,
                amount_lookback=amount_lookback,
                amount_minimum_ratio=amount_minimum_ratio,
            )
            return service.preview_strategy(strategy)
        except Exception as exc:
            raise_public_tool_error(exc)

    @server.tool()
    async def start_backtest(
        dataset_id: str,
        symbol: str,
        start_date: str,
        end_date: str,
        submission_id: str,
        schema_version: int = 1,
        strategy_type: str = "ma_crossover",
        fast_window: int = 5,
        slow_window: int = 20,
        target_weight: float = 0.95,
        amount_lookback: int = 0,
        amount_minimum_ratio: float = 1.0,
        initial_cash: float = 1_000_000.0,
        price_mode: str = "",
        slippage: float = 0.001,
        commission: float = 0.00025,
        min_commission: float = 5.0,
        lot_size: int = 100,
        liquidity_cap_ratio: float = 0.8,
        daily_open_previous_day_ratio: float = 0.1,
        daily_close_previous_day_ratio: float = 0.1,
        risk_free_rate: float = 0.03,
    ) -> dict[str, Any]:
        """Validate data and queue an isolated daily cash backtest; never accepts Python code."""

        try:
            strategy = _strategy_from_flat_args(
                symbol=symbol,
                schema_version=schema_version,
                strategy_type=strategy_type,
                fast_window=fast_window,
                slow_window=slow_window,
                target_weight=target_weight,
                amount_lookback=amount_lookback,
                amount_minimum_ratio=amount_minimum_ratio,
            )
            backtest = BacktestSpec(
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                price_mode=price_mode or None,
                slippage=slippage,
                commission=commission,
                min_commission=min_commission,
                lot_size=lot_size,
                liquidity_cap_ratio=liquidity_cap_ratio,
                daily_open_previous_day_ratio=daily_open_previous_day_ratio,
                daily_close_previous_day_ratio=daily_close_previous_day_ratio,
                risk_free_rate=risk_free_rate,
            )
            return await _public_thread_call(
                service.start_backtest, dataset_id, strategy, backtest, submission_id
            )
        except Exception as exc:
            # ``_public_thread_call`` has already mapped service failures. This
            # second boundary covers the request-model construction above.
            if isinstance(exc, RuntimeError) and str(exc).startswith("DIEPI_MCP_"):
                raise
            raise_public_tool_error(exc)

    @server.tool()
    def job_status(job_id: str) -> dict[str, Any]:
        """Return persistent status for an opaque asynchronous backtest job."""

        return _public_call(service.job_status, job_id)

    @server.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Request cooperative cancellation; a running diePi engine publishes CANCELED evidence."""

        return _public_call(service.cancel_job, job_id)

    @server.tool()
    async def get_result(job_id: str) -> dict[str, Any]:
        """Re-verify a RunArtifact and return a bounded summary; only SUCCESS is rankable."""

        return await _public_thread_call(service.get_result, job_id)

    return server


def _strategy_from_flat_args(
    *,
    symbol: str,
    schema_version: int,
    strategy_type: str,
    fast_window: int,
    slow_window: int,
    target_weight: float,
    amount_lookback: int,
    amount_minimum_ratio: float,
) -> MaCrossoverSpec:
    if amount_lookback < 0:
        raise ValueError("amount_lookback must be 0 (disabled) or at least 2")
    amount_filter = None
    if amount_lookback != 0:
        amount_filter = AmountFilter(
            lookback=amount_lookback,
            minimum_ratio=amount_minimum_ratio,
        )
    return MaCrossoverSpec(
        schema_version=schema_version,
        strategy_type=strategy_type,
        symbol=symbol,
        fast_window=fast_window,
        slow_window=slow_window,
        target_weight=target_weight,
        amount_filter=amount_filter,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="diePi constrained MCP server")
    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser(
        "init-config",
        help="create a new adapter home containing deterministic synthetic data only",
    )
    init_parser.add_argument(
        "--target",
        required=True,
        help="new destination directory; existing paths are never overwritten",
    )
    parser.add_argument(
        "--config",
        help=f"trusted JSON config (otherwise read from {CONFIG_ENV})",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate configuration and exit without starting MCP",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "init-config":
            from .bootstrap import initialize_synthetic_home

            print(
                json.dumps(
                    initialize_synthetic_home(args.target),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return
        if args.check_config:
            config = load_config(args.config)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "schema_version": 1,
                        "datasets": sorted(config.datasets),
                        "max_concurrent_jobs": config.max_concurrent_jobs,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return
        create_server(args.config).run(transport="stdio")
    except Exception as exc:
        print(f"diepi-mcp startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()


__all__ = ["create_server", "main"]
