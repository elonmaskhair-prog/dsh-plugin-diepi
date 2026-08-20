import pytest
from pydantic import ValidationError

from diepi_mcp.models import AmountFilter, BacktestSpec, MaCrossoverSpec


def test_strategy_spec_normalizes_symbol_and_rejects_unknown_fields():
    spec = MaCrossoverSpec(symbol="510300.sh", fast_window=5, slow_window=20)
    assert spec.symbol == "510300.SH"
    with pytest.raises(ValidationError):
        MaCrossoverSpec(
            symbol="510300.SH",
            fast_window=5,
            slow_window=20,
            python="print('not allowed')",
        )


def test_strategy_spec_rejects_invalid_windows():
    with pytest.raises(ValidationError, match="fast_window"):
        MaCrossoverSpec(symbol="510300.SH", fast_window=20, slow_window=20)


def test_backtest_spec_validates_dates_and_liquidity_assumptions():
    spec = BacktestSpec(start_date="20260101", end_date="20260630")
    assert spec.daily_open_previous_day_ratio == 0.1
    assert spec.daily_close_previous_day_ratio == 0.1
    with pytest.raises(ValidationError, match="start_date"):
        BacktestSpec(start_date="20260201", end_date="20260101")


def test_amount_filter_is_bounded():
    with pytest.raises(ValidationError):
        AmountFilter(lookback=0, minimum_ratio=1.0)


def test_versioned_contract_rejects_numeric_string_coercion():
    with pytest.raises(ValidationError):
        MaCrossoverSpec(symbol="510300.SH", fast_window="5", slow_window=20)
    with pytest.raises(ValidationError):
        BacktestSpec(start_date="20260101", end_date="20260630", slippage="0.001")
