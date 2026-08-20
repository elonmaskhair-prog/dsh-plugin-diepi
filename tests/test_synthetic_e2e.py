from importlib.metadata import PackageNotFoundError, version
import subprocess
import sys
import time

import pytest

from diepi_mcp import __version__
from diepi_mcp.bootstrap import CONFIG_FILENAME, initialize_synthetic_home
from diepi_mcp.config import load_config
from diepi_mcp.integration import DEMO_END_DATE, DEMO_START_DATE, DEMO_SYMBOL
from diepi_mcp.models import BacktestSpec, MaCrossoverSpec
from diepi_mcp.service import QuantService


def _service(tmp_path):
    home = tmp_path / "adapter-home"
    initialize_synthetic_home(home)
    return QuantService(load_config(home / CONFIG_FILENAME))


def _wait(service, job_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.job_status(job_id)
        if status["state"] in {"succeeded", "failed", "canceled", "interrupted"}:
            return status
        time.sleep(0.05)
    raise AssertionError("synthetic job did not reach a terminal state")


def _require_current_wheel():
    try:
        installed = version("diepi-mcp")
    except PackageNotFoundError:
        installed = None
    probe = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import diepi_mcp; print(diepi_mcp.__version__)",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    isolated_version = probe.stdout.strip() if probe.returncode == 0 else None
    if installed != __version__ or isolated_version != __version__:
        pytest.skip("isolated worker E2E requires the current adapter wheel to be installed")


def test_synthetic_data_validates_and_runs_to_a_verified_result(tmp_path):
    _require_current_wheel()
    service = _service(tmp_path)
    try:
        validation = service.validate_data(
            "synthetic-demo",
            [DEMO_SYMBOL],
            DEMO_START_DATE,
            DEMO_END_DATE,
            "dual",
        )
        assert validation["contract_ready"] is True
        assert validation["dataset_kind"] == "synthetic_demo"

        started = service.start_backtest(
            "synthetic-demo",
            MaCrossoverSpec(symbol=DEMO_SYMBOL, fast_window=2, slow_window=5),
            BacktestSpec(start_date="20240115", end_date=DEMO_END_DATE),
            "req_synthetic_success_0001",
        )
        assert started["accepted"] is True
        status = _wait(service, started["job_id"])
        assert status["state"] == "succeeded"

        result = service.get_result(started["job_id"])
        assert result["result_status"] == "SUCCESS"
        assert result["artifact_verified"] is True
        assert result["adapter_attribution_verified"] is True
        assert result["result_committed"] is True
        assert result["rankable"] is True
        assert result["execution_request"]["strategy"]["symbol"] == DEMO_SYMBOL
    finally:
        service.close()


def test_synthetic_admission_supports_deterministic_queued_cancel(tmp_path, monkeypatch):
    # Hold the worker before service construction so cancellation is proven at
    # the durable queued boundary rather than relying on a timing race.
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    service = _service(tmp_path)
    try:
        started = service.start_backtest(
            "synthetic-demo",
            MaCrossoverSpec(symbol=DEMO_SYMBOL, fast_window=2, slow_window=5),
            BacktestSpec(start_date="20240115", end_date=DEMO_END_DATE),
            "req_synthetic_cancel_0001",
        )
        assert started["accepted"] is True
        assert service.job_status(started["job_id"])["state"] == "queued"

        canceled = service.cancel_job(started["job_id"])
        assert canceled["cancel_accepted"] is True
        assert canceled["state"] == "canceled"
        result = service.get_result(started["job_id"])
        assert result["artifact_available"] is False
        assert result["rankable"] is False
    finally:
        service.close()
