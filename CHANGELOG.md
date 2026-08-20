# Changelog

All notable changes are recorded here. This project follows Semantic
Versioning, including prerelease identifiers while the protocol is Alpha.

## [Unreleased]

## [0.1.0a1] - 2026-08-20

### Added

- Windows/Ubuntu release gates for Python artifacts and the packed DSH bundle.
- Community governance, private vulnerability-reporting instructions, package
  metadata, financial disclaimer, and third-party notices.
- A deterministic, generated-only synthetic onboarding path.

### Changed

- Python prerelease version is `0.1.0a1`; npm prerelease version is
  `0.1.0-alpha.1`.
- `diepi-mcp` now pins the qualified `diepi==0.1.1` contract.
- DSH rc.7 is the primary target and rc.8 is the canary target.
- Bundle installation targets the real `web` or `headless` profile.
- Tushare remains a manual, external Skill handoff with a reviewed upstream
  revision reference.

### Security

- Bundle activation now requires explicit absolute paths for both the MCP
  executable and adapter configuration; it no longer resolves `diepi-mcp`
  through ambient `PATH`.
- Documentation separates Agent-writable workspaces from host-owned config,
  state, results, executables, and datasets.

This is the initial Developer Preview of the constrained Python MCP adapter
and DSH bundle.

[Unreleased]: https://github.com/elonmaskhair-prog/dsh-plugin-diepi/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/elonmaskhair-prog/dsh-plugin-diepi/releases/tag/v0.1.0a1
