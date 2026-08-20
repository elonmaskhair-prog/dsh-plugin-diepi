"""Public, versioned request contracts exposed to an agent."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SYMBOL_RE = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")


def _parse_date(value: str, field: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError(f"{field} must use YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid calendar date") from exc
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AmountFilter(StrictModel):
    """Require yesterday's amount to exceed a prior-window average."""

    lookback: int = Field(default=20, ge=2, le=250)
    minimum_ratio: float = Field(default=1.0, ge=0.0, le=20.0)


class MaCrossoverSpec(StrictModel):
    """StrategySpec v1: one deterministic, compiler-owned strategy family."""

    schema_version: Literal[1] = 1
    strategy_type: Literal["ma_crossover"] = "ma_crossover"
    symbol: str = Field(description="Canonical A-share or ETF code, for example 510300.SH")
    fast_window: int = Field(default=5, ge=1, le=250)
    slow_window: int = Field(default=20, ge=2, le=500)
    target_weight: float = Field(default=0.95, gt=0.0, le=1.0)
    amount_filter: Optional[AmountFilter] = None

    @model_validator(mode="after")
    def validate_strategy(self) -> "MaCrossoverSpec":
        normalized = self.symbol.upper()
        if not _SYMBOL_RE.fullmatch(normalized):
            raise ValueError("symbol must look like 000001.SZ, 600000.SH, or 430047.BJ")
        if normalized != self.symbol:
            object.__setattr__(self, "symbol", normalized)
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        return self


class BacktestSpec(StrictModel):
    """Execution assumptions for the first daily cash-market MVP."""

    start_date: str = Field(description="Inclusive start date in YYYYMMDD")
    end_date: str = Field(description="Inclusive end date in YYYYMMDD")
    initial_cash: float = Field(default=1_000_000.0, ge=10_000.0, le=1e15)
    price_mode: Optional[Literal["dual", "raw", "hfq"]] = None
    slippage: float = Field(default=0.001, ge=0.0, le=0.1)
    commission: float = Field(default=0.00025, ge=0.0, le=0.1)
    min_commission: float = Field(default=5.0, ge=0.0, le=100_000.0)
    lot_size: int = Field(default=100, ge=1, le=1_000_000)
    liquidity_cap_ratio: float = Field(default=0.8, gt=0.0, le=1.0)
    daily_open_previous_day_ratio: float = Field(default=0.1, gt=0.0, le=1.0)
    daily_close_previous_day_ratio: float = Field(default=0.1, gt=0.0, le=1.0)
    risk_free_rate: float = Field(default=0.03, ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def validate_dates(self) -> "BacktestSpec":
        start = _parse_date(self.start_date, "start_date")
        end = _parse_date(self.end_date, "end_date")
        if start > end:
            raise ValueError("start_date must be on or before end_date")
        return self


__all__ = ["AmountFilter", "BacktestSpec", "MaCrossoverSpec"]
