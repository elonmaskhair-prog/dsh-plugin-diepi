"""Compiler-owned daily MA crossover strategy for StrategySpec v1.

The adapter overrides only the constants below. Agent-authored Python never
crosses this execution boundary.
"""

FAST_WINDOW = 5
SLOW_WINDOW = 20
TARGET_WEIGHT = 0.95
AMOUNT_LOOKBACK = 0
AMOUNT_MINIMUM_RATIO = 0.0

# Manifest-covered provenance bindings injected by the adapter worker.  The
# strategy never branches on them; declarations prevent silent/typo-prone
# runtime parameter injection.
DIEPI_ADAPTER_REQUEST_SHA256 = ""
DIEPI_ADAPTER_STRATEGY_SHA256 = ""
DIEPI_ADAPTER_TEMPLATE_SHA256 = ""
DIEPI_ADAPTER_VERSION = ""
DIEPI_ADAPTER_VALIDATION_SHA256 = ""
DIEPI_ADAPTER_SOURCES_SHA256 = ""
DIEPI_ADAPTER_VALIDATION_START_DATE = ""


def _cross(close):
    if close is None or len(close) < SLOW_WINDOW + 1:
        return False, False
    previous = close.iloc[:-1]
    previous_fast = previous.tail(FAST_WINDOW).mean()
    previous_slow = previous.tail(SLOW_WINDOW).mean()
    current_fast = close.tail(FAST_WINDOW).mean()
    current_slow = close.tail(SLOW_WINDOW).mean()
    return (
        bool(previous_fast <= previous_slow and current_fast > current_slow),
        bool(previous_fast >= previous_slow and current_fast < current_slow),
    )


def _amount_allows_entry(history):
    if AMOUNT_LOOKBACK <= 0:
        return True
    amount = history["amount"]
    if amount is None or len(amount) < AMOUNT_LOOKBACK + 1:
        return False
    latest = float(amount.iloc[-1])
    baseline = amount.iloc[-(AMOUNT_LOOKBACK + 1) : -1]
    if latest < 0 or len(baseline) != AMOUNT_LOOKBACK:
        return False
    average = float(baseline.mean())
    if average <= 0:
        return False
    return latest / average >= AMOUNT_MINIMUM_RATIO


def on_before_market_open(ctx):
    pool = ctx.get_stock_pool()
    required = max(SLOW_WINDOW + 1, AMOUNT_LOOKBACK + 1)
    for symbol in pool:
        history = ctx.get_daily(symbol, days=required)
        if history is None or len(history) < required:
            continue
        crossed_up, crossed_down = _cross(history["close"])
        position = ctx.get_position(symbol)
        has_position = position is not None and position.shares > 0
        if crossed_down and has_position:
            ctx.order_target_percent(
                symbol,
                0.0,
                when="open",
                note=f"STRATEGYSPEC_MA{FAST_WINDOW}_DOWN_MA{SLOW_WINDOW}",
            )
        elif crossed_up and not has_position and _amount_allows_entry(history):
            ctx.order_target_percent(
                symbol,
                TARGET_WEIGHT,
                when="open",
                note=f"STRATEGYSPEC_MA{FAST_WINDOW}_UP_MA{SLOW_WINDOW}",
            )
    return pool


def on_day(ctx, bars):
    pass


def on_after_market_close(ctx):
    pass
