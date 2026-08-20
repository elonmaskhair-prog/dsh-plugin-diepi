# Data contract for the MVP

The adapter does not infer market semantics from arbitrary column names. A
configured root must already satisfy diePi's `market_data_v1` layout and is
checked for the exact symbol/date/price-mode scope before every accepted run.

For daily StrategySpec v1, the economically meaningful minimum is:

- canonical instrument code and stock/ETF metadata;
- trading date and a trading calendar;
- finite OHLC with valid ordering and positive prices;
- volume and amount with declared units;
- a raw, adjusted, or aligned dual price lane;
- adjustment factors when dual mode is used;
- enough history for the slowest indicator and optional amount filter.

`start_backtest` does not validate only the requested performance window. It
finds the first trading day in that window and extends the validation start to
cover every completed bar visible to the first pre-open callback. For the
current strict crossover this is `max(slow_window + 1, amount_lookback + 1)`
bars. A calendar gap or a newly listed symbol with too little history fails
before the job is accepted.

In diePi's current canonical data, daily `amount` is expressed in thousand yuan,
while minute `amount` is expressed in yuan. A ratio filter is unit-invariant,
but liquidity rules and any future absolute threshold are not. Never silently
mix the two.

## Grades

| Grade | Config price mode | Meaning |
|---|---|---|
| `dual` | `dual` | adjusted strategy observations + raw matching prices, strictly aligned |
| `raw_only` | `raw` | raw observations and matching; corporate-action jumps remain in indicators |
| `adjusted_only` | `hfq` | adjusted observations and matching; execution is approximate |

`validate_data` proves structural/causal contract readiness. It does not prove
data licensing, source authenticity, survivorship-bias freedom or economic
correctness.

## Validation-to-execution file identity

For an accepted backtest, a separate content-identity check surrounds structural
validation. Before any warm-up read, the adapter asks diePi for the
`SourceFingerprint` records of the dataset manifest, the actual parsed trading
calendar and every required direct daily-market-data lane selected for the
symbol, price mode and requested execution window. It repeats the collection
with fresh providers after validation. Both canonical identities and warm-up
scopes must match. The stable value becomes `validation_sources_sha256` in the
immutable execution request, while the complete canonical validation report is
persisted privately and authenticated by `validation_report_sha256`.

Dual mode requires direct adjusted and raw daily lanes plus the direct
adjustment-factor lane. Raw-only and adjusted-only modes require their
corresponding direct lane. The MVP rejects untracked section-file fallback: an
input that is not fingerprinted cannot silently supply matching or strategy
prices.

The worker recomputes this identity before calling `run_backtest`; a mismatch is
`DATA_SOURCE_IDENTITY_CHANGED` and no engine run starts. A completed
`RunArtifact` must also carry provenance whose source fingerprints reduce to
the same digest, and its `ResultContract` calendar assumptions must exactly
match the frozen validation report. Consequently, an artifact from another
dataset generation or a file changed between admission and worker start cannot
satisfy adapter attribution.

This is an integrity and attribution check, not proof that the vendor data is
true, licensed or free of bias. It also is not a filesystem transaction across
arbitrary concurrent writers. Production data roots must remain host-owned and
immutable for the duration of validation and execution; use snapshots or a
content-addressed dataset generation when stronger concurrent-update isolation
is required.

## Agent/Skill data handoff

Data acquisition is deliberately composable instead of being built into this
adapter. A user may prepare data manually or install a provider-specific Agent
Skill. For Tushare, point the user to the
[official Tushare Skill](https://github.com/waditu-tushare/skills); do not copy,
silently install or treat that external Skill as part of this package's trusted
execution boundary.

The packaged handoff records reviewed upstream commit
[`5e12b31d09123e262c5fb38564e80c26d05cb830`](https://github.com/waditu-tushare/skills/commit/5e12b31d09123e262c5fb38564e80c26d05cb830)
as an audit anchor. It is not a promise that upstream behavior, terms, or data
access remain unchanged; installation and upgrades require an explicit user
decision and a fresh review.

The upstream Skill owns only acquisition. It must write a new staging root that
already follows diePi `market_data_v1`; it must not mutate a configured active
dataset. The handoff is accepted only after all of the following:

1. Resolve the exact instrument type, symbol, date range and daily price mode.
2. Preserve source meaning while writing the canonical per-symbol Parquet
   layout, exact dtypes and daily `amount` in thousand yuan.
3. For `raw`, write the stock or ETF raw lane. For `dual`, write completely
   aligned raw and HFQ lanes plus the original positive adjustment-factor
   series, retaining its first-row anchor. Treat diePi AFI-1 as authoritative;
   an upstream label such as `pro_bar(adj='hfq')` is not proof of an identical
   price scale.
4. Do not invent suspension bars, fill missing factors, hide duplicate dates or
   silently take a raw/HFQ intersection.
5. Run `diepi data validate` against the exact symbol, storage interval and
   price mode, read the complete report, then have the host register and freeze
   the validated root behind an opaque `dataset_id`.
6. Call the MCP `validate_data` gate before every backtest as usual.

The Agent may load an already installed external Skill when data is missing. If
it is absent, present its official source and installation instructions and
stop; never auto-install code. If credentials are absent, instruct the user to
configure them outside the conversation. Never ask for, echo or persist a
Tushare token in a prompt, chat, MCP argument, dataset, log or artifact.

This protocol is provider-neutral. Other data Skills may participate when they
produce the same validated contract; no provider name weakens the boundary.
