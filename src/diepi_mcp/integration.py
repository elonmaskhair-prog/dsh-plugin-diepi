"""The exact public diePi integration contract required by this adapter."""

from __future__ import annotations

from diepi.integration import (
    ArtifactStore,
    DataProvider,
    DEMO_DATASET_ID,
    DEMO_END_DATE,
    DEMO_GENERATOR_VERSION,
    DEMO_START_DATE,
    DEMO_SYMBOL,
    DEMO_VALIDATION_FILENAME,
    DemoWorkspace,
    SourceFingerprint,
    TRADE_CALENDAR_SOURCE_KIND,
    TradeCalendarIdentity,
    collect_market_data_fingerprints,
    collect_trade_calendar_fingerprint,
    generate_synthetic_demo,
    require_complete_direct_sources,
    require_integration_contract,
    run_backtest,
    run_doctor,
    trade_calendar_fingerprint,
    validate_local_data,
)


REQUIRED_INTEGRATION_API_VERSION = 1
REQUIRED_INTEGRATION_CAPABILITIES = frozenset(
    {
        "diepi.artifact_binding.v1",
        "diepi.backtest.stop_check.v1",
        "diepi.calendar_identity.v1",
        "diepi.direct_source_fingerprints.v1",
        "diepi.doctor.v1",
        "diepi.local_data_validation.v1",
        "diepi.synthetic_demo.v1",
    }
)


def require_diepi_integration() -> None:
    """Fail before accepting work unless diePi exposes the complete v1 facade."""

    require_integration_contract(
        api_version=REQUIRED_INTEGRATION_API_VERSION,
        capabilities=REQUIRED_INTEGRATION_CAPABILITIES,
    )


__all__ = [
    "ArtifactStore",
    "DataProvider",
    "DEMO_DATASET_ID",
    "DEMO_END_DATE",
    "DEMO_GENERATOR_VERSION",
    "DEMO_START_DATE",
    "DEMO_SYMBOL",
    "DEMO_VALIDATION_FILENAME",
    "DemoWorkspace",
    "REQUIRED_INTEGRATION_API_VERSION",
    "REQUIRED_INTEGRATION_CAPABILITIES",
    "SourceFingerprint",
    "TRADE_CALENDAR_SOURCE_KIND",
    "TradeCalendarIdentity",
    "collect_market_data_fingerprints",
    "collect_trade_calendar_fingerprint",
    "generate_synthetic_demo",
    "require_complete_direct_sources",
    "require_diepi_integration",
    "run_backtest",
    "run_doctor",
    "trade_calendar_fingerprint",
    "validate_local_data",
]
