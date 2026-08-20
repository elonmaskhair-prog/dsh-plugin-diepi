import pytest
import pandas as pd

from diepi.artifacts import SourceFingerprint
from diepi.backtest.data.calendar import load_builtin_trade_calendar

import diepi_mcp.source_binding as source_binding


def _source(kind: str, path: str) -> SourceFingerprint:
    return SourceFingerprint.from_bytes(
        kind=kind,
        logical_path=path,
        payload=path.encode("utf-8"),
    )


def test_dual_source_identity_rejects_unfingerprinted_fallback_lane(tmp_path, monkeypatch):
    (tmp_path / "diepi_dataset.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        source_binding,
        "collect_trade_calendar_fingerprint",
        lambda root: _source("trade_calendar", "calendar/bundled.json"),
    )
    monkeypatch.setattr(
        source_binding,
        "collect_market_data_fingerprints",
        lambda *args, **kwargs: (
            _source(
                "market_data_file",
                "parquet/timeseries/etf_daily/510300.SH.parquet",
            ),
            _source(
                "market_data_file",
                "parquet/timeseries/etf_adj_factor/510300.SH.parquet",
            ),
        ),
    )

    with pytest.raises(ValueError, match="daily:raw"):
        source_binding.collect_source_identity(
            tmp_path,
            symbol="510300.SH",
            price_mode="dual",
            start_date="20260101",
            end_date="20260630",
        )


def test_source_identity_changes_when_local_calendar_override_appears(tmp_path):
    (tmp_path / "diepi_dataset.json").write_text("{}", encoding="utf-8")
    files = {
        "parquet/timeseries/etf_daily/510300.SH.parquet": {
            "trade_date": ["20260105"],
            "close": [1.0],
        },
        "parquet/timeseries/etf_daily_raw/510300.SH.parquet": {
            "trade_date": ["20260105"],
            "close": [1.0],
        },
        "parquet/timeseries/etf_adj_factor/510300.SH.parquet": {
            "trade_date": ["20260105"],
            "adj_factor": [1.0],
        },
    }
    for relative, payload in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(payload).to_parquet(path, index=False)

    _, bundled_digest = source_binding.collect_source_identity(
        tmp_path,
        symbol="510300.SH",
        price_mode="dual",
        start_date="20260101",
        end_date="20260630",
    )

    override = tmp_path / "parquet/metadata/common/trade_cal.parquet"
    override.parent.mkdir(parents=True, exist_ok=True)
    load_builtin_trade_calendar().to_parquet(override, index=False)
    _, local_digest = source_binding.collect_source_identity(
        tmp_path,
        symbol="510300.SH",
        price_mode="dual",
        start_date="20260101",
        end_date="20260630",
    )

    assert local_digest != bundled_digest
