## Problem and scope

<!-- What user-visible problem does this solve? What is intentionally out of scope? -->

## Trust and quantitative assumptions

<!-- Data roots, permissions, causality, matching, evidence, failure behavior. -->

## Verification

<!-- Exact commands and results. State whether built wheel/sdist/tgz were tested. -->

## Compatibility and payload

- [ ] I checked DSH rc.7 and rc.8 impact.
- [ ] I checked the exact diePi dependency contract.
- [ ] I inspected Python and npm package payloads.
- [ ] Tests and onboarding assets use generated synthetic data only.
- [ ] No credentials, sessions, databases, artifacts, or real market data are included.
- [ ] Documentation and `CHANGELOG.md` are updated where required.

## Safety

- [ ] This change does not expose paths, commands, SQL, environment variables, or arbitrary Python to the model.
- [ ] This change does not add live trading or broker credentials to the research boundary.
