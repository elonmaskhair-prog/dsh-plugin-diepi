# Architecture

## Ownership

DeepSeek Harness owns conversation, model reasoning, Skill instructions and
tool orchestration. This repository owns the typed Agent boundary and persistent
job metadata. diePi owns market-data validation, event causality, matching,
fees, accounting, `ResultContract` and `RunArtifact`.

Provider-specific external Skills may acquire data before this request path.
They are not trusted adapter components and never change diePi's contract. They
write a new staging root; the host registers it behind an opaque `dataset_id`
only after exact-scope diePi validation. The official Tushare Skill is one such
optional upstream Skill, not a bundled dependency or a runtime provider.

The Python adapter and DSH bundle are deliberately separate packages. Other MCP
hosts can use `diepi-mcp` without DSH, while Harness-specific churn remains in
the small `dsh/` directory.

## Request path

1. A trusted JSON configuration maps an opaque `dataset_id` to a validated,
   host-owned data root and results root.
2. The model submits a closed Pydantic schema. Unknown fields are rejected.
3. `start_backtest` resolves the first trading day, extends validation across
   every pre-start bar the strategy can read, and rejects insufficient history.
4. The adapter fingerprints the selected dataset manifest, the actual parsed
   trading calendar and every required direct market-data lane both before and
   after structural validation. The two identities must match; their canonical digest becomes
   `validation_sources_sha256`.
5. A caller-chosen `submission_id` is atomically bound to the canonical
   execution request. Retrying the same pair returns the original job.
6. The adapter freezes the request, the full canonical validation-report
   preimage and its digest, the source digest, effective price mode, strategy
   spec and compiler-template digest in a private job directory.
7. A daemon worker launches the current Python interpreter with an argv array,
   no shell, isolated import mode, a minimal environment allowlist and a
   compiler-owned strategy file. Before entering diePi it recomputes the source
   identity and rejects data changed since admission.
8. diePi builds and verifies the complete artifact in a same-parent staging
   directory, then atomically publishes the immutable run directory.
9. After process exit, the parent reloads and binds the artifact itself. A CAS
   transition commits the terminal job state together with the canonical
   manifest SHA-256.
10. `get_result` calls `ArtifactStore.load()` again, requires the current manifest
    to match that committed digest, and returns canonical, bounded,
    path-private evidence. Artifact status/contract, metrics, diagnostics and
    producer text remain withheld unless both attribution and commit succeed.

## Stable contracts

- Adapter configuration schema v1
- StrategySpec v1
- MCP tool input/output schemas
- opaque `job_id`, immutable `run_id`, request and strategy digests
- validation report/source digests and committed artifact manifest digest
- diePi `ResultContract` and `RunArtifact`

The DSH native API is intentionally not a business-logic dependency. The bundle
only mounts the shipped MCP client and a packaged Skill, which limits exposure
to breaking Harness internals while it remains a developer preview.

## Jobs and cancellation

SQLite survives adapter restarts, while each backtest runs in its own process.
Cancellation creates a sentinel that is polled by diePi through
`PortfolioEngine.stop_check`. A cooperative stop therefore produces a canonical
`CANCELED` result rather than pretending a killed process is a complete run.

Every state transition is compare-and-swap guarded. A fixed runtime limit and
cancel grace period terminate an unresponsive worker and mark the job failed;
only cooperative cancellation may publish canonical `CANCELED` evidence. If
the adapter restarts, every previously active state becomes `interrupted` and a
later orphan artifact is never promoted back to a committed success.

## Validation-to-execution source binding

The complete canonical validation report is persisted in the private job
directory and authenticated by its digest. `validation_sources_sha256`
separately identifies every execution input the MVP admits. It is the SHA-256
of a canonically sorted list of diePi `SourceFingerprint` records for the
dataset manifest, the actual parsed trading-calendar identity and the selected
market-data inputs. Each record contains the logical path and content identity;
absolute host paths are not part of the model-visible contract.

The adapter captures this identity on both sides of structural validation and
rejects a changed generation before queueing. This ensures the report and the
identity describe the same stable source generation under the immutable-root
trust assumption.

The MVP requires a complete direct-v1 source set. Dual mode requires adjusted
and raw daily lanes plus the adjustment-factor lane; single-lane modes require
their corresponding direct lane. Untracked section-file fallback is rejected
rather than silently becoming an execution input outside the evidence chain.

The source identity is present in the canonical execution-request digest and is
injected into diePi's manifest-covered `strategy_params`. The worker recomputes
it immediately before execution. During parent acceptance, artifact provenance
must yield the same identity. In parallel, the strategy digest binds the typed
strategy spec to the compiler-owned template SHA-256, and the parent hashes the
artifact's copied `strategy_source` member to require that exact template.

These checks close ordinary validation-to-execution substitution and artifact
mix-up paths. They still rely on host-owned immutable data roots; they are not a
filesystem snapshot or an OS isolation boundary against a privileged writer
racing individual reads.

The MCP executable and JSON config, SQLite state root, results root, and data
root are separate trust anchors and must remain outside the Agent-writable
workspace. If the Agent can rewrite the database and every artifact preimage
together, content hashes alone cannot distinguish fabricated evidence. The DSH
bundle therefore requires explicit absolute command/config paths instead of an
ambient `PATH` lookup.

## Parent terminal acceptance

There are two ordered commits:

1. diePi writes payloads and `manifest.json` under staging, reloads them to
   verify hashes and adapter invariants, then atomically renames staging to the
   final run directory.
2. For an artifact-bearing terminal output, the adapter parent validates the
   worker-result/exit-code pair, reloads the frozen validation report and the
   published artifact, compares its terminal contract, full request
   attribution, market provenance and calendar assumptions, computes the
   canonical manifest digest, then atomically transitions the SQLite job state
   and stores that digest. Worker-level errors and forced termination commit
   only failure.

The worker cannot directly write a successful database state. A successful
artifact that arrives after restart, timeout, forced termination or an accepted
cancel is not promoted. Result reads must reproduce the committed manifest digest.

## Coordinated diePi release gate

`diepi-mcp 0.1.0a1` pins the qualified `diepi==0.1.1` distribution containing
`run_backtest(..., stop_check=...)`, source-fingerprint provenance, calendar
identity, and atomic artifact behavior. The runtime introspection check remains
defense in depth; it is not a substitute for the exact release dependency.

A future wider range requires a stable diePi adapter facade plus clean
wheel/sdist compatibility tests. MCP also stays on the reviewed 1.29 line for
this Alpha; an MCP 2.x migration belongs to a separately qualified release.
