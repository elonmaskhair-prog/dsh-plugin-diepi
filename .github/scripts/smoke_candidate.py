"""Run one deterministic adapter backtest against diePi's generated demo."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from diepi_mcp.config import load_config
from diepi_mcp.models import BacktestSpec, MaCrossoverSpec
from diepi_mcp.service import QuantService


def _run(service: QuantService) -> int:
    strategy = MaCrossoverSpec(
        symbol="000001.SZ",
        fast_window=2,
        slow_window=5,
        target_weight=0.5,
    )
    backtest = BacktestSpec(
        start_date="20240115",
        end_date="20240216",
        price_mode="dual",
    )
    submission_id = "req_ci_synthetic_candidate_0001"
    capabilities = service.capabilities()
    if capabilities["adapter_version"] != "0.1.0a1":
        raise AssertionError(capabilities["adapter_version"])
    if capabilities["diepi_version"] != "0.1.1":
        raise AssertionError(capabilities["diepi_version"])

    validation = service.validate_data(
        "synthetic-demo",
        ["000001.SZ"],
        "20240102",
        "20240216",
        "dual",
    )
    if not validation["contract_ready"]:
        raise AssertionError(validation)

    started = service.start_backtest(
        "synthetic-demo", strategy, backtest, submission_id
    )
    if not started["accepted"]:
        raise AssertionError(started)
    repeated = service.start_backtest(
        "synthetic-demo", strategy, backtest, submission_id
    )
    if repeated["job_id"] != started["job_id"]:
        raise AssertionError("idempotent submission created a second job")

    deadline = time.monotonic() + 90.0
    status = service.job_status(started["job_id"])
    while status["state"] not in {
        "succeeded",
        "failed",
        "canceled",
        "interrupted",
    }:
        if time.monotonic() >= deadline:
            raise AssertionError("candidate backtest did not finish in 90 seconds")
        time.sleep(0.05)
        status = service.job_status(started["job_id"])
    if status["state"] != "succeeded":
        raise AssertionError(status)

    result = service.get_result(started["job_id"])
    required = {
        "artifact_verified": True,
        "adapter_attribution_verified": True,
        "result_committed": True,
        "result_status": "SUCCESS",
        "rankable": True,
    }
    for key, expected in required.items():
        if result.get(key) != expected:
            raise AssertionError({key: result.get(key), "result": result})
    print(
        json.dumps(
            {
                "job_id": started["job_id"],
                "run_id": result["run_id"],
                "request_digest": started["request_digest"],
                "manifest_sha256": result["manifest_sha256"],
                "metrics": result["metrics"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    config_path = Path(os.environ["DIEPI_MCP_CONFIG"]).resolve(strict=True)
    service = QuantService(load_config(config_path))
    try:
        return _run(service)
    finally:
        service.jobs.close()


if __name__ == "__main__":
    raise SystemExit(main())
