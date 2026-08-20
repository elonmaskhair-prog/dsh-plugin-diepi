import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from diepi.artifacts import ArtifactStore
from diepi_mcp.config import load_config
from diepi_mcp.models import BacktestSpec, MaCrossoverSpec
from diepi_mcp.service import QuantService


def _service(tmp_path):
    raw = os.environ.get("DIEPI_MCP_TEST_DATA_ROOT")
    if not raw:
        pytest.skip("set DIEPI_MCP_TEST_DATA_ROOT to run the integration fixture")
    data_root = Path(raw).resolve(strict=True)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state_root": str(tmp_path / "state"),
                "datasets": {
                    "fixture": {
                        "data_root": str(data_root),
                        "results_root": str(tmp_path / "results"),
                        "data_grade": "dual",
                        "default_price_mode": "dual",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return QuantService(load_config(config))


def _wait(service, job_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.job_status(job_id)
        if status["state"] in {"succeeded", "failed", "canceled", "interrupted"}:
            return status
        time.sleep(0.05)
    raise AssertionError("job did not reach a terminal state")


@pytest.mark.integration
def test_fixed_strategy_runs_and_returns_verified_rankable_artifact(tmp_path):
    service = _service(tmp_path)
    started = service.start_backtest(
        "fixture",
        MaCrossoverSpec(symbol="510300.SH", fast_window=5, slow_window=20),
        BacktestSpec(start_date="20260301", end_date="20260630"),
        "req_e2e_success_0001",
    )
    assert started["accepted"] is True
    status = _wait(service, started["job_id"])
    assert status["state"] == "succeeded"
    result = service.get_result(started["job_id"])
    assert result["artifact_verified"] is True
    assert result["adapter_attribution_verified"] is True
    assert result["result_committed"] is True
    assert result["result_status"] == "SUCCESS"
    assert result["rankable"] is True
    assert result["metrics_source"] == "verified_canonical_result"
    loaded = ArtifactStore.load(tmp_path / "results" / result["run_id"])
    assert result["metrics"]["final_asset"] == loaded.result.final_value
    assert result["execution_stats"] == loaded.result.execution_stats
    assert result["execution_request"]["validation_start_date"] < "20260301"
    assert "artifact_dir" not in result

    validation_path = tmp_path / "state" / "jobs" / started["job_id"] / "validation-report.json"
    original_validation = validation_path.read_bytes()
    validation_payload = json.loads(original_validation)
    validation_payload["calendar"]["calendar_id"] += "-tampered"
    validation_path.write_text(
        json.dumps(
            validation_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    validation_tampered = service.get_result(started["job_id"])
    assert validation_tampered["adapter_attribution_verified"] is False
    assert validation_tampered["result_committed"] is True
    assert validation_tampered["rankable"] is False
    assert validation_tampered["metrics"] == {}
    assert validation_tampered["execution_stats"] == {}
    assert validation_tampered["result_status"] is None
    assert validation_tampered["result_contract"] is None
    assert validation_tampered["diagnostic"] is None
    assert validation_tampered["producer"] is None
    validation_path.write_bytes(original_validation)

    with sqlite3.connect(tmp_path / "state" / "jobs.sqlite3") as connection:
        connection.execute(
            "UPDATE jobs SET request_digest = ? WHERE job_id = ?",
            ("f" * 64, started["job_id"]),
        )
    mismatched = service.get_result(started["job_id"])
    assert mismatched["artifact_verified"] is True
    assert mismatched["adapter_attribution_verified"] is False
    assert mismatched["rankable"] is False
    assert mismatched["metrics"] == {}
    assert mismatched["execution_stats"] == {}
    assert mismatched["result_contract"] is None

    with sqlite3.connect(tmp_path / "state" / "jobs.sqlite3") as connection:
        connection.execute(
            "UPDATE jobs SET request_digest = ? WHERE job_id = ?",
            (started["request_digest"], started["job_id"]),
        )
    manifest_path = tmp_path / "results" / result["run_id"] / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["producer"]["platform"] += "-post-commit-tamper"
    manifest_path.write_text(
        json.dumps(
            manifest_payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    tampered = service.get_result(started["job_id"])
    assert tampered["artifact_verified"] is True
    assert tampered["adapter_attribution_verified"] is False
    assert tampered["result_committed"] is False
    assert tampered["rankable"] is False
    assert tampered["metrics"] == {}
    assert tampered["execution_stats"] == {}
    assert tampered["result_contract"] is None
    assert "job.artifact_manifest_sha256" in tampered["attribution_error"]["fields"]


@pytest.mark.integration
def test_running_job_can_publish_canonical_canceled_artifact(tmp_path):
    service = _service(tmp_path)
    started = service.start_backtest(
        "fixture",
        MaCrossoverSpec(symbol="510300.SH", fast_window=3, slow_window=30),
        BacktestSpec(start_date="20260301", end_date="20260630"),
        "req_e2e_cancel_0001",
    )
    assert started["accepted"] is True
    deadline = time.monotonic() + 10.0
    while service.job_status(started["job_id"])["state"] == "queued":
        if time.monotonic() > deadline:
            raise AssertionError("job did not start")
        time.sleep(0.01)
    canceled = service.cancel_job(started["job_id"])
    assert canceled["cancel_accepted"] is True
    status = _wait(service, started["job_id"])
    assert status["state"] == "canceled"
    result = service.get_result(started["job_id"])
    assert result["artifact_verified"] is True
    assert result["adapter_attribution_verified"] is True
    assert result["result_committed"] is True
    assert result["result_status"] == "CANCELED"
    assert result["rankable"] is False
