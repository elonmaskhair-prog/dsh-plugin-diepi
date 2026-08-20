"""Deterministic StrategySpec compiler and human-readable strategy cards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import MaCrossoverSpec


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def template_digest() -> str:
    """Hash the exact compiler-owned source executed by diePi."""

    source = Path(__file__).with_name("strategies") / "ma_crossover.py"
    # Text mode matches diePi's strategy loader, including universal-newline
    # normalization on Windows; the artifact stores these exact UTF-8 bytes.
    executed_source = source.read_text(encoding="utf-8").encode("utf-8")
    return hashlib.sha256(executed_source).hexdigest()


def strategy_digest_for_template(spec: MaCrossoverSpec, template_sha256: str) -> str:
    if (
        type(template_sha256) is not str
        or len(template_sha256) != 64
        or any(char not in "0123456789abcdef" for char in template_sha256)
    ):
        raise ValueError("template_sha256 must be a lowercase SHA-256 digest")
    binding = {
        "strategy_spec": spec.model_dump(mode="json"),
        "template_sha256": template_sha256,
    }
    return hashlib.sha256(canonical_json(binding)).hexdigest()


def strategy_digest(spec: MaCrossoverSpec) -> str:
    return strategy_digest_for_template(spec, template_digest())


def strategy_parameters(spec: MaCrossoverSpec) -> dict[str, Any]:
    amount = spec.amount_filter
    return {
        "FAST_WINDOW": spec.fast_window,
        "SLOW_WINDOW": spec.slow_window,
        "TARGET_WEIGHT": spec.target_weight,
        "AMOUNT_LOOKBACK": amount.lookback if amount is not None else 0,
        "AMOUNT_MINIMUM_RATIO": amount.minimum_ratio if amount is not None else 0.0,
    }


def required_history_bars(spec: MaCrossoverSpec) -> int:
    """Return completed daily bars needed at the first pre-open callback.

    A strict crossover compares moving averages ending at T-2 and T-1, so the
    slow window needs one additional completed observation.  The optional
    amount filter has the same current-versus-prior-window shape.
    """

    amount = spec.amount_filter
    return max(
        spec.slow_window + 1,
        (amount.lookback + 1) if amount is not None else 0,
    )


def strategy_card(spec: MaCrossoverSpec) -> dict[str, Any]:
    amount = spec.amount_filter
    lookback = required_history_bars(spec)
    entry_conditions = [
        f"MA({spec.fast_window}) crosses above MA({spec.slow_window}) using closes through T-1"
    ]
    if amount is not None:
        entry_conditions.append(
            "T-1 amount / mean(amount from T-2 backwards over "
            f"{amount.lookback} bars) >= {amount.minimum_ratio:g}"
        )
    return {
        "schema_version": 1,
        "strategy_digest": strategy_digest(spec),
        "template_sha256": template_digest(),
        "canonical_spec": spec.model_dump(mode="json"),
        "universe": [spec.symbol],
        "frequency": "daily",
        "decision_point": "before_open_T",
        "information_boundary": "daily bars completed through T-1 only",
        "entry": {
            "conditions": entry_conditions,
            "target_weight": spec.target_weight,
            "execution": "T open, subject to diePi matching and cash constraints",
        },
        "exit": {
            "conditions": [
                f"MA({spec.fast_window}) crosses below MA({spec.slow_window}) using closes through T-1"
            ],
            "target_weight": 0.0,
            "execution": "T open, subject to diePi matching and market constraints",
        },
        "minimum_history_bars": lookback,
        "positioning": "long-only, no leverage, one symbol",
        "important_semantics": [
            "A rejected strict-crossover order is not retried merely because the fast MA stays above/below.",
            "The amount filter applies to entries only; exits do not require amount expansion.",
            "No same-day close/high/low/amount is visible at the T pre-open decision point.",
            "Target weight is submitted only on a crossover event; there is no daily rebalancing or retry.",
            "The backtest does not force liquidation at the end; final value marks remaining holdings to market.",
        ],
        "warning": "Research use only. Preview is not a backtest and is not investment advice.",
    }


__all__ = [
    "canonical_json",
    "required_history_bars",
    "strategy_card",
    "strategy_digest",
    "strategy_digest_for_template",
    "strategy_parameters",
    "template_digest",
]
