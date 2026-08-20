import pandas as pd

from diepi_mcp.models import AmountFilter, MaCrossoverSpec
from diepi_mcp.strategy import (
    required_history_bars,
    strategy_card,
    strategy_digest,
    strategy_parameters,
    template_digest,
)
from diepi_mcp.strategies import ma_crossover


def test_strategy_digest_is_canonical_and_card_is_causal():
    left = MaCrossoverSpec(
        symbol="510300.SH",
        fast_window=5,
        slow_window=20,
        amount_filter=AmountFilter(lookback=10, minimum_ratio=1.5),
    )
    right = MaCrossoverSpec.model_validate(left.model_dump())
    assert strategy_digest(left) == strategy_digest(right)
    card = strategy_card(left)
    assert card["decision_point"] == "before_open_T"
    assert card["information_boundary"] == "daily bars completed through T-1 only"
    assert card["minimum_history_bars"] == 21
    assert card["template_sha256"] == template_digest()


def test_compiler_parameters_contain_values_not_code():
    spec = MaCrossoverSpec(symbol="510300.SH", fast_window=3, slow_window=8)
    assert strategy_parameters(spec) == {
        "FAST_WINDOW": 3,
        "SLOW_WINDOW": 8,
        "TARGET_WEIGHT": 0.95,
        "AMOUNT_LOOKBACK": 0,
        "AMOUNT_MINIMUM_RATIO": 0.0,
    }


def test_required_history_uses_larger_crossover_or_amount_window():
    crossover_dominates = MaCrossoverSpec(
        symbol="510300.SH",
        fast_window=3,
        slow_window=20,
        amount_filter=AmountFilter(lookback=10),
    )
    amount_dominates = MaCrossoverSpec(
        symbol="510300.SH",
        fast_window=3,
        slow_window=8,
        amount_filter=AmountFilter(lookback=12),
    )
    assert required_history_bars(crossover_dominates) == 21
    assert required_history_bars(amount_dominates) == 13


def test_amount_filter_compares_latest_with_prior_window(monkeypatch):
    monkeypatch.setattr(ma_crossover, "AMOUNT_LOOKBACK", 3)
    monkeypatch.setattr(ma_crossover, "AMOUNT_MINIMUM_RATIO", 2.0)
    frame = pd.DataFrame({"amount": [10.0, 10.0, 10.0, 20.0]})
    assert ma_crossover._amount_allows_entry(frame) is True
    frame.loc[3, "amount"] = 19.9
    assert ma_crossover._amount_allows_entry(frame) is False


def test_amount_filter_includes_zero_amount_in_prior_window(monkeypatch):
    monkeypatch.setattr(ma_crossover, "AMOUNT_LOOKBACK", 3)
    monkeypatch.setattr(ma_crossover, "AMOUNT_MINIMUM_RATIO", 2.0)
    frame = pd.DataFrame({"amount": [0.0, 10.0, 20.0, 20.0]})
    assert ma_crossover._amount_allows_entry(frame) is True
