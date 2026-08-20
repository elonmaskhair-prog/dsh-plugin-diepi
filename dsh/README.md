# dsh-plugin-diepi

Community DSH bundle for the constrained `diepi-mcp` quantitative-research
adapter. It mounts the `diepi-quant-research` Skill and connects DSH to a
separately installed MCP stdio server.

This package is orchestration glue, not the diePi engine. It supports typed,
daily stock/ETF research backtests only. It contains no market data, provider
connector, credential handling, arbitrary-Python tool, broker integration, or
live-order capability.

This is an independent community project. It is compatible with DeepSeek
Harness but is not affiliated with or endorsed by DeepSeek.

## Compatibility

- DSH `0.1.0-rc.7`: primary Alpha target
- DSH `0.1.0-rc.8`: canary target; use a separate `DSH_HOME`
- Node.js `^22.19.0 || >=24.0.0`
- pnpm `11.7.x`, available as the bare `pnpm` command on `PATH`
- Python 3.10+
- `diepi-mcp==0.1.0a1`, which pins `diepi==0.1.1`

DSH supplies `@deepseek-ai/dsh-mcp-client` and
`@deepseek-ai/dsh-skill-filesystem`; this bundle intentionally does not copy
them.

## Required host setup

Install the Python adapter into a dedicated, host-owned environment. Initialize
a brand-new adapter home with:

```powershell
diepi-mcp init-config --target "C:\host-owned\diepi\adapter-home-alpha1"
```

The command exclusively claims a new target, writes an
`.diepi-mcp-incomplete` marker, creates each staged payload without replacing
an existing path, and commits `diepi-mcp.json` last. It calls diePi's demo
generator and includes no real market rows. A failure deliberately leaves an
unusable incomplete target rather than deleting or taking over a racing path;
choose a new directory, or have the host owner inspect it before manual
cleanup. Keep the adapter home and virtual environment outside the Agent
workspace.

Before DSH starts, set both variables to absolute paths:

```powershell
$env:DIEPI_MCP_COMMAND = (Resolve-Path "C:\host-owned\diepi\venv\Scripts\diepi-mcp.exe").Path
$env:DIEPI_MCP_CONFIG = (Resolve-Path "C:\host-owned\diepi\adapter-home-alpha1\diepi-mcp.json").Path
& $env:DIEPI_MCP_COMMAND --check-config
```

The bundle validates these values during activation. Missing or relative paths
fail closed; there is no fallback to a `diepi-mcp` executable found through
`PATH`. MCP starts with the configuration directory as its working directory,
not the Agent workspace.

Prepare the package manager used internally by `dsh plugin`:

```powershell
corepack enable
corepack prepare pnpm@11.7.0 --activate
pnpm --version
```

If Corepack is absent from the selected Node installation, install it using its
official instructions first.

## Install into a real app profile

For the Web experience:

```powershell
dsh plugin --profile web add dsh-plugin-diepi@0.1.0-alpha.1
dsh --profile web --dump-config
dsh web --no-open
```

For headless:

```powershell
dsh plugin --profile headless add dsh-plugin-diepi@0.1.0-alpha.1
dsh --profile headless --dump-config
dsh --profile headless
```

Do not install only into an arbitrary custom profile: a newly named profile has
the base bundle but no Web or headless app. Plugin membership changes apply to
the next DSH process, so restart after add/remove/update.

From a source checkout, run `dsh plugin --profile web add ./dsh` at the
repository root. The npm package lives in `dsh/`; a Git dependency aimed only
at the repository root is not a supported installation path.

## Optional external data Skill

The bundle never downloads data or installs provider code. When configured
data cannot satisfy a request, the research Skill may explain how the user can
manually install the official Tushare Skill. That upstream code is outside this
package's trust boundary and must run under the host's own permission policy.

Acquisition writes a new staging generation. A trusted host then validates the
exact `market_data_v1` scope, registers an opaque `dataset_id`, makes the data
immutable, and restarts MCP. Provider credentials stay outside prompts, MCP
arguments, logs, datasets, and artifacts.

See
[`references/tushare-data-handoff.md`](skills/diepi-quant-research/references/tushare-data-handoff.md)
for the reviewed-revision reference and consent boundary.

## MCP timeout and retry safety

The bundle uses a 10-minute default server tool timeout. Hosts can set
`DIEPI_MCP_TOOL_CALL_TIMEOUT_MS`; values are clamped to 300,000 through
2,147,483,647 milliseconds, and invalid values fall back to 600,000.

`start_backtest` requires a `submission_id` matching
`req_[A-Za-z0-9_-]{8,64}`. If a call times out or the transport fails, retry the
identical request with the same ID. Reusing that ID for different arguments is
an error. This prevents an unknown response from silently enqueueing a second
job.

Accept a run for comparison only when all of these are true:

```text
artifact_verified
adapter_attribution_verified
result_committed
result_status == SUCCESS
rankable
```

## Trust boundary and disclaimer

MCP stdio runs as trusted host code outside the Agent file sandbox. The MCP
executable, JSON configuration, dataset roots, state database, and results
roots must not be Agent-writable. This adapter provides evidence binding, not
an OS sandbox or proof of market-data truth/licensing.

Research and backtesting only. Outputs are not investment advice, a trading
offer, or a promise of future performance.

Apache-2.0. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
