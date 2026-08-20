# Security policy and trust model

This project is an Alpha research adapter. Only the latest Alpha receives
security fixes; older prereleases are unsupported.

## Report a vulnerability

Use GitHub's private vulnerability-reporting form:

<https://github.com/elonmaskhair-prog/dsh-plugin-diepi/security/advisories/new>

Do not open a public issue, attach a real provider token, or include third-party
market data in a report. Include the affected Python/npm versions, operating
system, DSH version, a minimal synthetic reproduction, impact, and any proposed
embargo date. Maintainers will acknowledge a valid report as capacity permits;
this volunteer project does not promise a fixed response SLA.

## Trusted components

- the host-owned JSON configuration;
- the installed `diepi-mcp` and diePi packages;
- the packaged compiler-owned strategy templates;
- the DeepSeek Harness bundle and MCP client command.

`DIEPI_MCP_COMMAND` and `DIEPI_MCP_CONFIG` are mandatory absolute paths. Bundle
activation fails rather than resolving an executable through `PATH` or guessing
a configuration file. The MCP process working directory is the host-owned
configuration directory, not the Agent workspace.

MCP stdio executes outside the Agent's file sandbox. Installing an npm or
Python package also executes trusted host code. Pin releases or commits and
review source before enabling the bundle.

Provider-specific external Skills are outside this project's trust boundary.
For example, the optional official Tushare Skill may use network access and
arbitrary Python under the host's Agent policy. This project links to it but
does not vendor or auto-install it. Run acquisition only into a new staging
root, review and validate the output, then register a frozen dataset; never let
an acquisition Skill mutate a configured active dataset during a run.

Keep provider credentials out of conversations and out of `diepi-mcp`. A
missing token is a host-setup condition, not a reason for the Agent to request
that the user paste a secret into chat. The adapter has no tool or configuration
field for a Tushare token.

## Enforced boundaries

- Tools accept dataset IDs, typed strategy values and opaque job IDs, never
  arbitrary paths, commands, environment variables, SQL or Python source.
- Unknown StrategySpec/config fields fail validation.
- Backtests use argv without a shell, Python isolated mode and a minimal child
  environment allowlist; import hooks, proxies, PATH and credential variables
  are not inherited.
- Data roots are treated as immutable; state/results roots may not live under them.
- Admission persists and authenticates the complete validation report and
  freezes the dataset manifest, actual parsed trading calendar and complete
  direct market-data source set selected for the run. The worker recomputes
  that identity before entering diePi, and the parent later requires the
  verified artifact provenance and calendar assumptions to reproduce the same
  evidence.
- Model-visible results omit absolute paths and full logs. Metrics, execution
  statistics, artifact status/contract, diagnostics and producer text remain
  withheld until both request attribution and the durable terminal commit pass.
- A run is not rankable until the complete RunArtifact verifies, matches the
  durable request/template/source fingerprints/parameters, and the job state
  committed success against the same manifest digest.

## Evidence and commit boundary

A worker result is evidence, not authority to commit a successful job. diePi
first writes every artifact member and `manifest.json` into a same-parent staging
directory, reloads it to verify hashes and adapter semantics, and only then
atomically renames that directory to the final immutable run path.

After the worker exits, the adapter parent independently checks the bounded
worker-result schema and exit-code mapping. For an artifact-bearing terminal
output it calls `ArtifactStore.load()`, verifies the terminal status,
request/run/template/execution-parameter attribution, the authenticated
validation report, market-data provenance and calendar assumptions, and
computes the canonical manifest SHA-256. Only then does one compare-and-swap
SQLite transition store the terminal state and that digest. Worker-level
errors and forced termination commit failure without a manifest and can never be
rankable. `get_result` loads an artifact again and requires its current manifest
digest to equal the committed digest. A missing, partial, replaced or late orphan
artifact therefore cannot turn an interrupted or failed job into a rankable success.

The source fingerprints detect a changed file identity at the pre-execution and
artifact-attribution boundaries; they do not turn a writable data directory
into a transactional filesystem snapshot. Keep configured data roots immutable
and inaccessible to the Agent and worker account except for required reads.
The JSON config, MCP executable/environment, SQLite state root, and results root
must also be outside the Agent-writable workspace. If the model can modify all
of those roots together, hashes can be recomputed around fabricated evidence.

## Coordinated dependency release

`diepi-mcp 0.1.0a1` pins `diepi==0.1.1`. The adapter relies on that release's
`run_backtest(..., stop_check=...)`, direct-source provenance, calendar identity,
and staging-verified atomic artifact publication. Do not widen this range until
diePi exposes and qualifies a stable compatibility facade. The runtime
`stop_check` signature gate remains defense in depth against a broken wheel.

MCP remains pinned to `mcp==1.29.0` with `pydantic>=2.12,<3` and
`pydantic-settings>=2.12,<2.15`. The settings upper bound avoids a verified
schema-construction regression; warnings are not suppressed. MCP 2.x is a
separate, potentially breaking migration and is not part of this Alpha line.

## Not a sandbox

diePi's general Python strategy interface executes trusted Python and is not a
security sandbox. This adapter does not expose that interface. Process isolation
improves failure containment but is not an OS security boundary. For multi-user
or hostile workloads, add a dedicated low-privilege account/container, network
denial, CPU/memory quotas and an authenticated service boundary.

There is no live trading capability. Do not add broker credentials to this
process. A future order system requires a separate permission domain,
deterministic risk checks, idempotency, previews with expiry, human approval and
an independent kill switch.

## Explicitly outside the security claim

- truth, licensing, completeness, or bias of user-provided market data;
- profitability or investment suitability of any strategy or result;
- containment from a privileged host user or administrator;
- arbitrary Python executed directly through diePi outside this adapter;
- external acquisition Skills, provider SDKs, DSH itself, or their networks.
