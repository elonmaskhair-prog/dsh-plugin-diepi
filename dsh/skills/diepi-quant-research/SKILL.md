---
name: diepi-quant-research
description: Turn a natural-language daily A-share stock or ETF idea into a typed diePi StrategySpec, obtain missing Tushare data through an already-installed official Skill, validate market_data_v1 inputs, run an auditable backtest, and interpret only verified results.
metadata:
  version: 1
  execution: research-only
---

# diePi quantitative research

Use this workflow when the user wants to express a stock or ETF strategy in
natural language and backtest it with diePi.

## Hard boundary

- This integration is for research and backtesting only. It has no live-order tool.
- Never pass or execute arbitrary Python through diePi tools. Any optional
  external Skill runs separately under the host's own tool and permission policy.
- StrategySpec v1 currently supports one daily, long-only `ma_crossover`
  strategy with an optional amount-expansion entry filter.
- Do not claim support for futures, leverage, short selling, intraday or minute
  data, portfolio optimization, or a built-in data connector.
- Treat Tushare acquisition as an optional handoff to the independently
  installed official Tushare Skill. Never request, receive, echo, or log a
  Tushare token in chat or in diePi tool arguments.

## Workflow

1. Call `mcp__diepi__capabilities` and select an opaque `dataset_id`.
   Read its `data_contract` and `execution_model`; these are part of the
   strategy interpretation, not optional boilerplate. If no configured
   dataset can cover the requested daily instrument and interval, do not
   invent an ID: read
   [the Tushare handoff](references/tushare-data-handoff.md) and follow its
   missing-data branch.
2. Translate the user's words into `StrategySpec v1`. Do not invent a ticker,
   date interval, or economically material trading assumption. Ask if one is missing.
3. Call `mcp__diepi__preview_strategy`. Show the strategy card when its exact
   entry, exit, position, or information boundary could surprise the user.
4. Call `mcp__diepi__validate_data` for the exact symbol, interval, and price
   mode. If the configured data is ready, skip all acquisition. If required
   daily data is missing, read
   [the Tushare handoff](references/tushare-data-handoff.md) and follow it.
   Validate the staged and host-registered `market_data_v1` dataset again.
   Treat validation as a hard gate: never start a backtest after a failed or
   incomplete validation. A warning is evidence to disclose, not text to
   silently discard.
5. Generate and record one stable `submission_id` for this exact execution
   request. Use `req_` followed by 8 to 64 ASCII letters, digits, `_`, or `-`;
   a fresh 32-character hexadecimal nonce is a good suffix. Never reuse the ID
   for different dataset, strategy, backtest, or execution-assumption values.
6. Call `mcp__diepi__start_backtest` with that `submission_id`. It validates
   again and returns quickly with a `job_id`; it never waits for the whole run.
   If the call times out or its transport fails, retry the exact same arguments
   with the same `submission_id`. Never generate a replacement ID for a retry.
7. Poll `mcp__diepi__job_status` at a reasonable cadence. Use
   `mcp__diepi__cancel_job` when the user asks to stop.
8. Call `mcp__diepi__get_result`. Treat a run as comparable only when all are true:
   `artifact_verified == true`, `adapter_attribution_verified == true`,
   `result_committed == true`, `result_status == SUCCESS`, and `rankable == true`.

The adapter atomically binds a `submission_id` to the full execution request.
Repeating the same ID with the same request returns the original job, including
after an uncertain timeout. Reusing it with different arguments fails with a
conflict; inspect the arguments, and create a new ID only for an intentionally
new execution request.

## Data semantics

- Prefer a `dual` dataset: adjusted prices for strategy observations and raw
  prices for matching, with strict alignment and factors.
- Accept raw daily data as the minimum input. `raw_only` is valid when
  disclosed; indicators then contain corporate-action jumps.
- `adjusted_only` is an approximation for execution and must be described as such.
- Require direct raw, HFQ, and original adjustment-factor lanes for `dual`.
  Treat diePi's adjustment contract as the sole authority. Do not accept an
  upstream series as compatible merely because it is labelled `hfq`, including
  an arbitrary `pro_bar(adj='hfq')` result.
- Keep stock and ETF files in their separate `market_data_v1` routes. Do not
  acquire, transform, validate, or backtest minute data through this Skill.
- Never reinterpret daily and minute amount units, timestamps, adjustment
  meaning, or trading calendars from column names alone. The diePi data
  contract and validator decide readiness.
- A rankable dual run requires complete direct adjusted, raw, and adjustment-
  factor inputs plus the exact trading-calendar identity frozen at admission.
  The MVP rejects an untracked section-file fallback instead of treating it as
  equivalent evidence.

## Matching semantics

- This is a bar-based cash-account model, not an order-book, queue-position,
  probability-of-fill, or market-impact simulator.
- A pre-open decision on T sees completed strategy-price bars only through
  T-1. Its target-percent DAY order is first eligible at T open on the
  execution-price lane; unfilled remainder expires at day end.
- Dual mode means adjusted prices for observations and strictly aligned raw
  prices for matching. Directional slippage, instrument/date-aware ticks,
  price limits, lot rules, T+0/T+1, cash/position availability, fees, and
  explicit liquidity caps are enforced by diePi.
- Open/close auction capacity uses an explicit fraction of previous-day
  amount. It is a capacity ceiling, not an estimated impact curve.
- The template submits only on a strict crossover. If that order is rejected,
  it is not retried merely because the fast average remains on the same side.
- Holdings are marked to market at the end; there is no forced liquidation.
- For an individual run, the verified `ResultContract.assumptions` and
  execution statistics are authoritative. Disclose them when they materially
  affect interpretation; do not replace them with generic market folklore.

## Research discipline

- State fees, slippage, position weight, price mode, date range, and causal
  decision point alongside results.
- Do not rank a failed, partial, invalid, canceled, or unverified run.
- Do not compare runs with different data grades or execution assumptions as
  if they differed only by strategy.
- Repeatedly searching parameters creates multiple-testing risk. Record the
  candidate set and preserve an out-of-sample interval.
- Results are research evidence, not investment advice or a promise of future returns.
