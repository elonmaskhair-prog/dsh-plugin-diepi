# Tushare data handoff

Use this handoff only when `mcp__diepi__capabilities` has no suitable configured
dataset, or when `mcp__diepi__validate_data` shows that the exact daily symbol,
interval, and price mode are unavailable. Do not refresh or replace data that
already passes validation unless the user explicitly asks.

## Use the official Skill without vendoring it

This project last reviewed upstream commit
[`5e12b31d09123e262c5fb38564e80c26d05cb830`](https://github.com/waditu-tushare/skills/commit/5e12b31d09123e262c5fb38564e80c26d05cb830).
That reference is an audit anchor, not a compatibility guarantee or an
endorsement. Upstream `master`, provider behavior, dependencies, terms, and
data access can change independently. Review the selected revision again
before use.

1. Detect whether the official Tushare Skill is already installed in the
   current agent environment.
2. If it is not installed, show the user the official repository, reviewed
   revision, and manual installation procedure, then stop the acquisition
   workflow. Do not clone or run any installation command automatically.

   - Repository: <https://github.com/waditu-tushare/skills>
   - Reviewed tree:
     <https://github.com/waditu-tushare/skills/tree/5e12b31d09123e262c5fb38564e80c26d05cb830>

   A user who explicitly chooses that reviewed revision can run the following
   from the same project root that will launch DSH on Windows:

   ```powershell
   $skillCheckout = Join-Path $env:LOCALAPPDATA "diepi-external-skills\tushare-5e12b31d"
   New-Item -ItemType Directory -Force -Path (Split-Path $skillCheckout) | Out-Null
   git clone --no-checkout https://github.com/waditu-tushare/skills.git $skillCheckout
   git -C $skillCheckout checkout --detach 5e12b31d09123e262c5fb38564e80c26d05cb830
   git -C $skillCheckout rev-parse HEAD
   npx --yes skills@1.5.23 add $skillCheckout --skill tushare --agent universal --copy --yes
   if (-not (Test-Path ".agents\skills\tushare\SKILL.md")) {
     throw "Tushare Skill was not installed; stop before data acquisition"
   }
   ```

   The full `rev-parse` output must equal the reviewed commit before
   installation. Choose a new checkout path if that directory already exists;
   do not overwrite an unrelated checkout. `skills@1.5.23` is pinned because a
   floating installer would defeat the revision anchor. After installation,
   verify both `.agents/skills/tushare/SKILL.md` and the `tushare` entry in the
   local Skill catalog; stop if either is absent.

3. If it is installed, use it as the external acquisition capability. Do not
   copy its files into this plugin and do not treat it as a diePi connector.
   The reviewed source folder/slug is `tushare-data`, but its frontmatter name,
   `skills@1.5.23` selector, installed directory, and DSH catalog name are
   `tushare`. The upstream README currently shows `--skill tushare-data`;
   end-to-end probes against this pinned CLI and commit found that spelling
   prints `No matching skills found`, exits zero, and installs nothing. Do not
   treat a zero installer exit code as proof; the file/catalog checks above are
   mandatory.
4. If its local credential is not configured, direct the user to the official
   out-of-band setup instructions and stop. Never ask the user to paste a token
   into chat; never expose a token in a prompt, tool argument, output, log, data
   file, manifest, or result artifact.

The manual install command puts a copied Skill under the project's `.agents/skills`
root so the DSH base provider can discover it without relying on interactive
agent detection or Windows symlink privileges. It does not provision runtime
dependencies or credentials. Before launching DSH, have the host install
`tushare` and a Parquet engine such as `pyarrow` into the Python interpreter the
Agent will call. Do not rely on an inherited `TUSHARE_TOKEN`: DSH scrubs
token-named variables from spawned tool processes. Configure and verify the
official SDK's host-local credential in a trusted terminal outside the
conversation.

## Produce a daily `market_data_v1` generation

Acquire the exact symbol and sufficient source history, including strategy
warm-up. Write a new staging generation; do not overwrite a configured dataset
or mutate files used by a running backtest. Have the trusted host register the
completed root under an opaque `dataset_id` before MCP validation.

For stock raw input, write:

```text
parquet/timeseries/daily_raw/{ts_code}.parquet
```

For ETF raw input, write the separate route:

```text
parquet/timeseries/etf_daily_raw/{ts_code}.parquet
```

Use canonical codes such as `600000.SH` or `510300.SH`. Provide at least
`trade_date, open, high, low, close, pre_close, amount`; write finite numeric
prices and nonnegative daily `amount` in thousand yuan. Keep dates unique and
strictly increasing. Preserve suspension gaps instead of synthesizing or
forward-filling bars.

Raw daily data is the minimum accepted handoff and must run as `raw_only` /
`price_mode=raw`. Disclose that indicators can contain corporate-action jumps.

For `dual`, provide all three direct lanes:

| Instrument | Raw | HFQ | Original factor |
| --- | --- | --- | --- |
| Stock | `daily_raw` | `daily` | `adj_factor` |
| ETF/LOF | `etf_daily_raw` | `etf_daily` | `etf_adj_factor` |

Keep raw and HFQ date keys identical. Preserve the source adjustment factor and
its first-row anchor. Apply diePi's factor identity to every adjusted price
field:

```text
base_factor = first source adj_factor
hfq_price   = raw_price * (adj_factor / base_factor)
```

Do not replace missing factors with `1`, forward-fill them, or reset the anchor
at the requested backtest start. Do not assume that Tushare
`pro_bar(adj='hfq')`, or any other upstream series named HFQ, has diePi's
normalization. diePi's contract and validator are the sole adjustment authority.

Do not use this handoff for minute data.

## Enforce the gate

Follow this sequence without skipping steps:

```text
missing daily data
  -> already-installed official Tushare Skill acquisition
  -> new staged market_data_v1 generation
  -> trusted-host dataset_id registration
  -> mcp__diepi__validate_data for the exact scope
  -> backtest only after validation succeeds
```

Stop on an unknown instrument type, amount unit, adjustment meaning, missing
factor, schema error, or validation failure. Never infer readiness from the
acquisition Skill's prose or from a successful download alone.

Treat registration as an explicit session boundary. The current MCP has no
tool that edits its trusted dataset configuration. After native diePi
validation succeeds, stop and ask the host owner to add the new root to the
JSON configuration and restart `diepi-mcp`/DSH. Resume with `capabilities` and
MCP `validate_data` in the next session; do not claim that acquisition and
backtesting can finish in one unattended headless turn.
