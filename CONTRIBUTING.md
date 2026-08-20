# Contributing

Thank you for improving the diePi DSH community integration. This repository
is deliberately small: DSH owns orchestration, `diepi-mcp` owns the constrained
Agent boundary, and diePi owns quantitative execution semantics.

## Before opening a change

- Use an issue for a material protocol, security-boundary, data-contract, or
  matching-semantics proposal.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- Do not submit provider credentials, private sessions, generated state,
  copyrighted datasets, or real market-data samples. Tests and onboarding
  assets must be deterministically synthetic.
- Do not add broker access or live trading to an apparently harmless research
  tool. That requires a separate threat model and permission domain.
- Keep provider acquisition optional. External Skills must not be vendored,
  auto-installed, or treated as trusted adapter dependencies.

## Development setup

Use Python 3.10+ and a clean virtual environment. The Alpha dependency is
intentionally exact: `diepi-mcp 0.1.0a1` is qualified with `diepi 0.1.1`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev]"
.\.venv\Scripts\python.exe -m pytest -m "not integration"
.\.venv\Scripts\ruff.exe check src tests
```

Integration tests must use data generated in a temporary directory. Never point
CI at `DIEPI_MCP_TEST_DATA_ROOT` containing redistributed market data.

For the DSH package:

```powershell
Push-Location dsh
npm pack --dry-run
Pop-Location
```

Before requesting review, also build the Python artifacts and inspect them:

```powershell
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check dist\*
```

## Change discipline

- Add or update tests for observable behavior.
- Keep MCP inputs closed and typed; never add arbitrary paths, shell commands,
  environment variables, SQL, or Python source to model-facing schemas.
- Preserve fail-closed startup, idempotent submission, cancellation, source
  binding, and parent-side artifact acceptance.
- Update `CHANGELOG.md`, user documentation, Skill instructions, package
  metadata, and both Python/npm versions together when a release-facing
  contract changes.
- Keep lines focused and commits reviewable. Explain security and economic
  assumptions in the pull request, not only in code comments.

## Pull requests

A pull request should state:

1. the user-visible problem and scope;
2. the trust or quantitative assumptions affected;
3. tests run and their exact results;
4. whether package payloads or dependencies changed;
5. any compatibility impact on DSH rc.7/rc.8 or diePi.

By contributing, you agree that your contribution is provided under this
repository's Apache-2.0 license and that you have the right to submit it.
