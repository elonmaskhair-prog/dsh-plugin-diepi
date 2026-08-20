from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from diepi.artifacts import EngineKind, RunOutcome, RunProvenance, SourceFingerprint
from diepi.backtest.data.calendar import TradeCalendarIdentity
from diepi.backtest.data.source_evidence import trade_calendar_fingerprint
from diepi.backtest.result_contract import (
    ActualInterval,
    DataCoverage,
    ResultAssumption,
    ResultContract,
    ResultStatus,
)

from diepi_mcp.artifact_binding import artifact_binding_errors
from diepi_mcp.jobs import build_public_request, request_digest
from diepi_mcp.models import BacktestSpec, MaCrossoverSpec
from diepi_mcp.source_binding import source_identity_sha256
from diepi_mcp.strategy import strategy_digest, strategy_parameters


def _binding_fixture():
    strategy = MaCrossoverSpec(
        symbol="510300.SH",
        fast_window=5,
        slow_window=20,
        target_weight=0.9,
    )
    backtest = BacktestSpec(start_date="20240101", end_date="20241231")
    calendar = {
        "source": "bundled",
        "calendar_id": "fixture-calendar",
        "version": "1",
        "content_sha256": "f" * 64,
        "coverage_start": "20230101",
        "coverage_end": "20251231",
        "rows": 1096,
        "open_days": 730,
    }
    sources = (
        SourceFingerprint.from_bytes(
            kind="dataset_manifest",
            logical_path="diepi_dataset.json",
            payload=b"fixture-manifest-v1",
        ),
        SourceFingerprint.from_bytes(
            kind="market_data",
            logical_path="daily/510300.SH.parquet",
            payload=b"fixture-market-data-v1",
        ),
        trade_calendar_fingerprint(TradeCalendarIdentity(**calendar)),
    )
    validation_sources_sha256 = source_identity_sha256(sources)
    public = build_public_request(
        "fixture",
        strategy,
        backtest,
        resolved_price_mode="dual",
        validation_report_sha256="a" * 64,
        validation_sources_sha256=validation_sources_sha256,
        validation_start_date="20231130",
    )
    request_sha256 = request_digest(public)
    strategy_sha256 = strategy_digest(strategy)
    request = {
        **public,
        "run_id": "dsh_20260818T120000Z_123456abcdef",
        "data_root": r"D:\data\fixture",
        "results_root": r"D:\results\fixture",
        "request_digest": request_sha256,
        "strategy_digest": strategy_sha256,
        "template_digest": public["template_sha256"],
    }
    record = {
        "run_id": request["run_id"],
        "dataset_id": "fixture",
        "request_digest": request_sha256,
        "strategy_digest": strategy_sha256,
    }
    metadata = {
        "DIEPI_ADAPTER_REQUEST_SHA256": request_sha256,
        "DIEPI_ADAPTER_STRATEGY_SHA256": strategy_sha256,
        "DIEPI_ADAPTER_TEMPLATE_SHA256": public["template_sha256"],
        "DIEPI_ADAPTER_VERSION": public["adapter_version"],
        "DIEPI_ADAPTER_VALIDATION_SHA256": "a" * 64,
        "DIEPI_ADAPTER_SOURCES_SHA256": validation_sources_sha256,
        "DIEPI_ADAPTER_VALIDATION_START_DATE": "20231130",
    }
    config = {
        "command": "run",
        "engine_kind": "cash_portfolio",
        "input_mode": "strategy",
        "strategy_file": "inputs/strategy.py",
        "strategy_name": "ma_crossover",
        "requested_start_date": backtest.start_date,
        "requested_end_date": backtest.end_date,
        "realized_symbols": [strategy.symbol],
        "parameters": {
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
            "initial_cash": backtest.initial_cash,
            "slippage": backtest.slippage,
            "commission": backtest.commission,
            "min_commission": backtest.min_commission,
            "lot_size": backtest.lot_size,
            "liquidity_cap_ratio": backtest.liquidity_cap_ratio,
            "daily_open_previous_day_ratio": backtest.daily_open_previous_day_ratio,
            "daily_close_previous_day_ratio": backtest.daily_close_previous_day_ratio,
            "risk_free_rate": backtest.risk_free_rate,
            "price_mode": "dual",
            "pool_symbols": [strategy.symbol],
            "strategy_params": {**strategy_parameters(strategy), **metadata},
        },
    }
    source = (
        (Path(__file__).parents[1] / "src" / "diepi_mcp" / "strategies" / "ma_crossover.py")
        .read_text(encoding="utf-8")
        .encode("utf-8")
    )
    assumptions = tuple(
        ResultAssumption(key=key, value=value)
        for key, value in {
            "execution.commission_rate": str(backtest.commission),
            "execution.slippage_rate": str(backtest.slippage),
            "execution.min_commission": str(backtest.min_commission),
            "execution.lot_size": str(backtest.lot_size),
            "execution.liquidity_cap_ratio": str(backtest.liquidity_cap_ratio),
            "execution.transfer_fee_rate": "0.0",
            "metrics.risk_free_rate": str(backtest.risk_free_rate),
            "metrics.trading_days_per_year": "252",
            "execution.frequency": "daily",
            "execution.stamp_duty_policy": "auto",
            "execution.strategy_price_mode": "hfq",
            "execution.execution_price_mode": "raw",
            "liquidity.daily_open_cap": (
                f"previous_day_ratio:{backtest.daily_open_previous_day_ratio}"
            ),
            "liquidity.daily_close_cap": (
                f"previous_day_ratio:{backtest.daily_close_previous_day_ratio}"
            ),
            "calendar.source": calendar["source"],
            "calendar.id": calendar["calendar_id"],
            "calendar.version": calendar["version"],
            "calendar.content_sha256": calendar["content_sha256"],
            "calendar.coverage_start": calendar["coverage_start"],
            "calendar.coverage_end": calendar["coverage_end"],
            "calendar.rows": str(calendar["rows"]),
            "calendar.open_days": str(calendar["open_days"]),
        }.items()
    )
    result_contract = ResultContract(
        status=ResultStatus.SUCCESS,
        assumptions=assumptions,
        actual_interval=ActualInterval(
            start_date="2024-01-01",
            end_date="2024-12-31",
        ),
        data_coverage=DataCoverage(
            expected_observations=1,
            actual_observations=1,
            ratio=1.0,
        ),
    )
    result = SimpleNamespace(
        start_date=backtest.start_date,
        end_date=backtest.end_date,
        initial_cash=backtest.initial_cash,
        result_contract=result_contract,
    )
    loaded = SimpleNamespace(
        manifest=SimpleNamespace(run_id=request["run_id"]),
        outcome=RunOutcome(
            engine_kind=EngineKind.CASH_PORTFOLIO,
            result_contract=result_contract,
            result_role="result",
            result=result,
        ),
        result=result,
        provenance=RunProvenance.build(sources=sources),
        config=config,
        read_bytes=lambda role: (
            source if role == "strategy_source" else (_ for _ in ()).throw(KeyError(role))
        ),
    )
    return (
        loaded,
        record,
        request,
        {
            "scope": {
                "symbols": [strategy.symbol],
                "start_date": request["validation_start_date"],
                "end_date": backtest.end_date,
                "frequency": "daily",
                "price_mode": request["resolved_price_mode"],
            },
            "calendar": calendar,
        },
    )


def test_verified_artifact_is_bound_to_exact_execution_request():
    loaded, record, request, validation_report = _binding_fixture()
    assert artifact_binding_errors(loaded, record, request, validation_report) == ()


def test_execution_parameter_mismatch_fails_attribution():
    loaded, record, request, validation_report = _binding_fixture()
    changed = SimpleNamespace(
        manifest=loaded.manifest,
        outcome=loaded.outcome,
        result=loaded.result,
        provenance=loaded.provenance,
        config=deepcopy(loaded.config),
        read_bytes=loaded.read_bytes,
    )
    changed.config["parameters"]["commission"] = 0.01
    assert "config.parameters.commission" in artifact_binding_errors(
        changed, record, request, validation_report
    )


def test_result_payload_mismatch_fails_attribution():
    loaded, record, request, validation_report = _binding_fixture()
    changed_result = SimpleNamespace(
        start_date=loaded.result.start_date,
        end_date=loaded.result.end_date,
        initial_cash=loaded.result.initial_cash + 1,
        result_contract=loaded.result.result_contract,
    )
    changed = SimpleNamespace(
        manifest=loaded.manifest,
        outcome=RunOutcome(
            engine_kind=loaded.outcome.engine_kind,
            result_contract=loaded.outcome.result_contract,
            result_role="result",
            result=changed_result,
        ),
        result=changed_result,
        provenance=loaded.provenance,
        config=loaded.config,
        read_bytes=loaded.read_bytes,
    )
    assert "result.initial_cash" in artifact_binding_errors(
        changed, record, request, validation_report
    )


def test_provenance_generation_mismatch_fails_attribution():
    loaded, record, request, validation_report = _binding_fixture()
    changed_sources = tuple(
        sorted(
            (
                *(source for source in loaded.provenance.sources if source.kind != "market_data"),
                SourceFingerprint.from_bytes(
                    kind="market_data",
                    logical_path="daily/510300.SH.parquet",
                    payload=b"fixture-market-data-v2",
                ),
            ),
            key=lambda source: (source.kind, source.logical_path),
        )
    )
    changed = SimpleNamespace(
        manifest=loaded.manifest,
        outcome=loaded.outcome,
        result=loaded.result,
        provenance=RunProvenance.build(sources=changed_sources),
        config=loaded.config,
        read_bytes=loaded.read_bytes,
    )
    assert "provenance.sources" in artifact_binding_errors(
        changed, record, request, validation_report
    )


def test_execution_calendar_must_match_the_frozen_validation_report():
    loaded, record, request, validation_report = _binding_fixture()
    changed_report = deepcopy(validation_report)
    changed_report["calendar"]["source"] = "local_override"

    assert "result_contract.assumptions.calendar.source" in artifact_binding_errors(
        loaded, record, request, changed_report
    )
    assert "provenance.trade_calendar" in artifact_binding_errors(
        loaded, record, request, changed_report
    )


def test_validation_scope_must_match_the_execution_request():
    loaded, record, request, validation_report = _binding_fixture()
    changed_report = deepcopy(validation_report)
    changed_report["scope"]["start_date"] = "20240101"

    assert "validation_report.scope.start_date" in artifact_binding_errors(
        loaded, record, request, changed_report
    )
