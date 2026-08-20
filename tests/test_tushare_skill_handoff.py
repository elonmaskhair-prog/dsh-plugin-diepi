"""Offline contract tests for the agent-driven Tushare-to-diePi handoff.

These tests intentionally exercise neither Tushare nor a diePi data connector.
They model the files an independently installed Tushare Skill can leave in the
public diePi layout, then ask the real adapter service and diePi validator to
admit or reject those files.
"""

from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

import pandas as pd
import pytest

from diepi_mcp.config import DatasetConfig
from diepi_mcp.integration import validate_local_data
from diepi_mcp.service import QuantService


_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SKILL_ROOT = _PLUGIN_ROOT / "dsh" / "skills" / "diepi-quant-research"
_OFFICIAL_TUSHARE_SKILL = "https://github.com/waditu-tushare/skills"
_DATES = ("20240102", "20240103", "20240104")
_PRICE_FIELDS = ("open", "high", "low", "close", "pre_close")


def _tushare_daily(symbol: str) -> pd.DataFrame:
    """Return a chronological, otherwise Tushare-shaped daily response."""

    return pd.DataFrame(
        {
            "ts_code": [symbol] * 3,
            "trade_date": list(_DATES),
            "open": [10.0, 10.2, 10.6],
            "high": [10.8, 10.9, 11.2],
            "low": [9.8, 10.0, 10.4],
            "close": [10.2, 10.6, 11.0],
            "pre_close": [9.9, 10.2, 10.6],
            "change": [0.3, 0.4, 0.4],
            "pct_chg": [3.0303, 3.9216, 3.7736],
            "vol": [120_000.0, 150_000.0, 180_000.0],
            # Tushare daily/fund_daily amount is already thousand yuan, which
            # is the diePi daily source unit.
            "amount": [12_000.0, 15_500.0, 19_800.0],
        }
    )


def _write_lane(data_root: Path, directory: str, symbol: str, frame: pd.DataFrame) -> Path:
    target = data_root / "parquet" / "timeseries" / directory / f"{symbol}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    return target


def _service(data_root: Path, *, data_grade: str, price_mode: str) -> QuantService:
    """Build only the read-only portion of QuantService used by validate_data."""

    dataset = DatasetConfig(
        dataset_id="tushare_fixture",
        description="offline Tushare Skill handoff fixture",
        data_root=data_root,
        results_root=data_root.parent / "results",
        data_grade=data_grade,
        default_price_mode=price_mode,
    )
    service = object.__new__(QuantService)
    service.config = SimpleNamespace(
        max_calendar_days=366,
        dataset=lambda dataset_id: (
            dataset
            if dataset_id == dataset.dataset_id
            else (_ for _ in ()).throw(ValueError("unknown dataset_id"))
        ),
    )
    return service


@pytest.mark.parametrize(
    ("symbol", "directory"),
    (
        pytest.param("600000.SH", "daily_raw", id="a-share-daily"),
        pytest.param("510300.SH", "etf_daily_raw", id="etf-fund-daily"),
    ),
)
def test_tushare_shaped_raw_daily_is_accepted_in_diepi_layout(
    tmp_path: Path,
    symbol: str,
    directory: str,
) -> None:
    data_root = tmp_path / "market-data"
    _write_lane(data_root, directory, symbol, _tushare_daily(symbol))
    service = _service(data_root, data_grade="raw_only", price_mode="raw")

    report = service.validate_data(
        "tushare_fixture",
        [symbol],
        _DATES[0],
        _DATES[-1],
    )
    underlying = validate_local_data(
        data_root=data_root,
        symbols=(symbol,),
        start_date=_DATES[0],
        end_date=_DATES[-1],
        frequency="daily",
        price_mode="raw",
        verify_manifest=True,
    ).to_dict()

    assert report["projection"] == "diepi_mcp.validation_public_v1"
    assert report["status"] == "pass"
    assert report["contract_ready"] is True
    assert report["selected_price_mode"] == "raw"
    assert report["manifest_status"] == "absent"
    assert report["dataset_kind"] == "user_supplied_unmanifested"
    assert len(report["pair_reports"]) == 1
    pair = report["pair_reports"][0]
    assert pair["status"] == "pass"
    assert pair["aligned_rows"] == len(_DATES)
    underlying_pair = underlying["pair_reports"][0]
    assert underlying_pair["strategy_price_space"] == "raw"
    assert underlying_pair["execution_price_space"] == "raw"
    assert underlying_pair["execution_amount_unit"] == "thousand_yuan"
    # Missing optional security-master metadata can remain visible as a
    # warning, but does not turn valid user-supplied bars into a rejection.
    assert not report["issues"] or all(
        issue["severity"] != "error" for issue in report["issues"]
    )


def test_tushare_shaped_dual_daily_passes_afi_1_and_rejects_price_mismatch(
    tmp_path: Path,
) -> None:
    symbol = "600000.SH"
    data_root = tmp_path / "market-data"
    raw = _tushare_daily(symbol)
    factors = pd.DataFrame(
        {
            "ts_code": [symbol] * 3,
            "trade_date": list(_DATES),
            "adj_factor": [1.0, 1.2, 1.2],
        }
    )
    hfq = raw.copy(deep=True)
    ratios = factors["adj_factor"] / factors["adj_factor"].iloc[0]
    for field in _PRICE_FIELDS:
        hfq[field] = raw[field] * ratios
    hfq["change"] = raw["change"] * ratios

    _write_lane(data_root, "daily_raw", symbol, raw)
    hfq_path = _write_lane(data_root, "daily", symbol, hfq)
    _write_lane(data_root, "adj_factor", symbol, factors)
    service = _service(data_root, data_grade="dual", price_mode="dual")

    accepted = service.validate_data(
        "tushare_fixture",
        [symbol],
        _DATES[0],
        _DATES[-1],
    )
    accepted_underlying = validate_local_data(
        data_root=data_root,
        symbols=(symbol,),
        start_date=_DATES[0],
        end_date=_DATES[-1],
        frequency="daily",
        price_mode="dual",
        verify_manifest=True,
    ).to_dict()

    assert accepted["contract_ready"] is True
    pair = accepted["pair_reports"][0]
    assert pair["status"] == "pass"
    assert accepted_underlying["contract_ready"] is True
    assert accepted_underlying["pair_reports"][0]["status"] == "pass"

    mismatched = hfq.copy(deep=True)
    mismatched.loc[1, "close"] += 0.1
    mismatched.to_parquet(hfq_path, index=False)

    rejected = service.validate_data(
        "tushare_fixture",
        [symbol],
        _DATES[0],
        _DATES[-1],
    )
    rejected_underlying = validate_local_data(
        data_root=data_root,
        symbols=(symbol,),
        start_date=_DATES[0],
        end_date=_DATES[-1],
        frequency="daily",
        price_mode="dual",
        verify_manifest=True,
    ).to_dict()

    assert rejected["status"] == "fail"
    assert rejected["contract_ready"] is False
    assert "MARKET_PAIR_CONTRACT_FAILED" in {
        issue["code"] for issue in rejected["issues"]
    }
    assert "PRICE_IDENTITY_MISMATCH" in {
        code for pair_report in rejected["pair_reports"] for code in pair_report["issue_codes"]
    }
    assert "PRICE_IDENTITY_MISMATCH" in {
        issue["code"]
        for pair_report in rejected_underlying["pair_reports"]
        for issue in pair_report["issues"]
    }


def test_packaged_skill_links_official_tushare_skill_with_consent_boundaries() -> None:
    markdown_paths = tuple(sorted(_SKILL_ROOT.rglob("*.md")))
    assert markdown_paths, "the packaged diePi Skill must exist"
    handoff_text = "\n".join(
        path.read_text(encoding="utf-8") for path in markdown_paths
    )

    assert _OFFICIAL_TUSHARE_SKILL in handoff_text
    assert re.search(
        r"(?:never|do not|must not)[^.\n]{0,120}"
        r"(?:auto(?:matically)?[- ]?install|install[^.\n]{0,40}without|"
        r"run[^.\n]{0,40}automatically)",
        handoff_text,
        flags=re.IGNORECASE,
    ), "the agent must not install the third-party Skill without explicit consent"
    assert re.search(
        r"(?:never|do not|must not)[^.\n]{0,160}(?:tushare\s+)?token"
        r"[^.\n]{0,160}(?:chat|conversation|prompt)",
        handoff_text,
        flags=re.IGNORECASE,
    ), "the agent must never collect a Tushare token through the model conversation"
