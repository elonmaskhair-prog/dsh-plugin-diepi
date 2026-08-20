"""Content identities binding admission validation to executed market data."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from .integration import (
    SourceFingerprint,
    collect_market_data_fingerprints,
    collect_trade_calendar_fingerprint,
    require_complete_direct_sources,
)

from .strategy import canonical_json


def source_identity_sha256(sources: Iterable[Any]) -> str:
    payload = sorted(
        (source.to_dict() for source in sources),
        key=lambda item: (item["kind"], item["logical_path"]),
    )
    if not payload:
        raise ValueError("market-data source identity is empty")
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def collect_source_identity(
    data_root: Path,
    *,
    symbol: str,
    price_mode: str,
    start_date: str,
    end_date: str,
) -> tuple[tuple[SourceFingerprint, ...], str]:
    """Freeze the exact direct files that diePi will fingerprint for this run."""

    root = Path(data_root).resolve(strict=True)
    manifest_path = root / "diepi_dataset.json"
    if not manifest_path.is_file():
        raise ValueError("verified dataset manifest is unavailable")
    sources = [
        SourceFingerprint.from_file(
            manifest_path,
            root=root,
            kind="dataset_manifest",
        ),
        collect_trade_calendar_fingerprint(root),
    ]
    market_sources = collect_market_data_fingerprints(
        root,
        symbols=(symbol,),
        price_mode=price_mode,
        frequency="daily",
        start_date=start_date,
        end_date=end_date,
    )
    if not market_sources:
        raise ValueError("direct market-data source identity is unavailable")
    require_complete_direct_sources(
        symbol,
        price_mode,
        market_sources,
        frequency="daily",
    )
    sources.extend(market_sources)
    unique = {(source.kind, source.logical_path): source for source in sources}
    frozen = tuple(sorted(unique.values(), key=lambda item: (item.kind, item.logical_path)))
    return frozen, source_identity_sha256(frozen)


__all__ = ["collect_source_identity", "source_identity_sha256"]
