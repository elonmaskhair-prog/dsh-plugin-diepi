# 0.1.0a1 Alpha release notes and gate

This document describes the source-tree Developer Preview. The Python package
is `diepi-mcp 0.1.0a1`; the DSH npm bundle is
`dsh-plugin-diepi 0.1.0-alpha.1`.

## Evidence-chain hardening

- Admission validates the requested interval plus every pre-start daily bar the
  packaged strategy can observe, persists the complete canonical validation
  report, and detects source changes across validation.
- The canonical request binds effective price mode, validation report,
  dataset/calendar/complete-direct-source fingerprints, adapter version,
  strategy spec, and packaged strategy-template SHA-256.
- A caller-supplied `submission_id` makes an unknown MCP timeout retry
  idempotent: the same request returns the original job and a different request
  conflicts.
- The isolated worker reproduces the market-data identity immediately before
  execution and records adapter attribution in manifest-covered parameters.
- diePi verifies a complete artifact under staging and atomically publishes it.
  The adapter parent independently loads and binds it before a CAS transition
  commits terminal state and canonical manifest SHA-256.
- Timeout, forced termination, adapter restart, and an
  accepted-but-unacknowledged cancellation cannot be promoted to a rankable
  success by a late artifact.

Existing job databases are migrated in place. Completed rows created before a
required evidence field existed do not gain trust retroactively; rerun them if
they must satisfy the current rankability contract.

## Coordinated dependencies

The Alpha pins `diepi==0.1.1`. That release contains the qualified
`run_backtest(..., stop_check=...)` callback, direct-source provenance,
calendar identity, and staging-verified atomic `RunArtifact` semantics used by
the adapter. Do not widen the dependency until those contracts are available
through a deliberately stable diePi compatibility facade.

This is also the first release-order gate: publish and independently install
`diepi 0.1.1` before running the plugin candidate workflow. A clean candidate
install is expected to fail while that exact dependency is unavailable; do not
work around the ordering by weakening the pin or using an ambient checkout.

The protocol stack remains `mcp==1.29.0`, `pydantic>=2.12,<3`, and
`pydantic-settings>=2.12,<2.15`. The settings upper bound avoids a verified
schema-construction regression without suppressing warnings. Migrating to MCP
2.x is explicitly deferred to a separately qualified, potentially breaking
adapter line.

## Onboarding and data boundary

The plugin ships no market data. The first-run path calls
`diepi-mcp init-config --target <new-directory>` outside the Agent workspace.
The wrapper exclusively claims a new host-owned directory, creates an
`.diepi-mcp-incomplete` marker, writes staged payloads without replacement,
and commits `diepi-mcp.json` last. It invokes diePi's deterministic demo
generator for invented data and refuses to merge with or overwrite an existing
target. Failure leaves an unusable incomplete directory for host inspection;
retry with a new path, or clean it manually only after checking ownership and
contents. The checked-in example configuration remains a schema reference,
not a bundled dataset.

Tushare is an optional manual Skill handoff. The plugin links a reviewed
upstream commit but does not vendor, auto-install, execute, update, or credential
that Skill. Host registration and MCP restart remain an explicit session
boundary after data validation.

## Blocking release matrix

Before uploading either package, test the exact built artifacts—not editable
checkouts or ambient `PYTHONPATH`—through all blocking cells:

| DSH | OS | Node | Profile | Status |
| --- | --- | --- | --- | --- |
| rc.7 | Windows | 22 | web | blocking |
| rc.7 | Ubuntu | 24 | headless | blocking |
| rc.8 | Windows | 22 | web | canary/blocking for this Alpha |
| rc.8 | Ubuntu | 24 | headless | canary/blocking for this Alpha |

The workflow first verifies the downloaded candidate against `SHA256SUMS` and
installs the sdist alone in a clean virtual environment for configuration and
deterministic synthetic adapter smoke. Each DSH cell then uses a fresh
`DSH_HOME`, a newly generated host-owned synthetic home, absolute MCP
command/config paths, the packed npm tgz, and the built Python wheel. Evidence
is deliberately split into three layers:

1. The installed wheel is contacted directly over MCP stdio to verify the
   exact eight raw tools plus `capabilities` and `preview_strategy`.
2. The installed adapter runs validation, idempotent admission, a successful
   deterministic backtest, and verified committed-result checks without an
   editable checkout or ambient `PYTHONPATH`.
3. The exact npm tgz is installed into the named profile and DSH is really
   booted. A keyless CI-only Loader probe strictly observes the eight
   `mcp__diepi__*` tools in `ctx.tools`, calls
   `mcp__diepi__capabilities`, emits a PASS sentinel, and requests clean DSH
   shutdown. `failOnStartupError` therefore participates in the runtime proof;
   `--dump-config` alone is not treated as startup evidence.

This packed-artifact matrix does not claim a model-mediated backtest, DSH-level
cancellation, MCP crash/reconnect recovery, or natural-language strategy
quality. Source tests cover the adapter state-machine cases; any release claim
about those end-to-end DSH behaviors needs a separately recorded manual gate.

Build once, test those exact files, record SHA-256 digests, and publish the same
files with manual approval. This repository's CI intentionally contains no
publish job.

## Scope reminders

Fingerprints prove identity, not vendor truth, licensing, absence of
survivorship bias, or investment suitability. The adapter remains a local
research boundary, not an OS sandbox and not a live-trading system.
