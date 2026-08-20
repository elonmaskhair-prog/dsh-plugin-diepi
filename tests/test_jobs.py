from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from diepi_mcp import __version__
from diepi_mcp.config import AdapterConfig, DatasetConfig
import diepi_mcp.jobs as jobs_module
from diepi_mcp.jobs import (
    JobManager,
    JobStore,
    _worker_result,
    build_public_request,
    load_private_request,
    load_validation_report,
    request_digest,
)
from diepi_mcp.models import BacktestSpec, MaCrossoverSpec
from diepi_mcp.strategy import canonical_json, strategy_digest, template_digest
import diepi_mcp.worker as worker_module


def _record(
    suffix: str,
    *,
    state: str = "queued",
    submission_id: str | None = None,
    request_sha256: str = "a" * 64,
) -> dict[str, str | None]:
    return {
        "job_id": f"job_{suffix}",
        "run_id": f"run_{suffix}",
        "dataset_id": "fixture",
        "state": state,
        "request_digest": request_sha256,
        "strategy_digest": "b" * 64,
        "created_at": "2026-01-01T00:00:00Z",
        "submission_id": submission_id,
    }


def _manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_jobs: int = 2,
    start_threads: bool = False,
):
    data_root = tmp_path / "data"
    results_root = tmp_path / "results"
    data_root.mkdir()
    dataset = DatasetConfig(
        dataset_id="fixture",
        description="fixture",
        data_root=data_root,
        results_root=results_root,
        data_grade="dual",
        default_price_mode="dual",
    )
    config = AdapterConfig(
        source=tmp_path / "config.json",
        state_root=tmp_path / "state",
        max_concurrent_jobs=1,
        max_queued_jobs=max_jobs,
        max_calendar_days=366,
        datasets={"fixture": dataset},
    )
    if not start_threads:
        monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    return JobManager(config), dataset


def _request_values():
    strategy = MaCrossoverSpec(symbol="510300.SH", fast_window=5, slow_window=20)
    backtest = BacktestSpec(start_date="20260101", end_date="20260131")
    return strategy, backtest


def _validation_report():
    payload = {
        "schema_version": 1,
        "status": "pass",
        "contract_ready": True,
        "scope": {"symbols": ["510300.SH"]},
        "dataset_kind": "user_supplied",
        "manifest_status": "verified",
        "manifest_sha256": "e" * 64,
        "calendar": {"status": "pass"},
        "pair_reports": [],
        "issues": [],
        "limitations": [],
    }
    return {
        **payload,
        "report_sha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
    }


def test_job_store_restart_marks_every_active_state_interrupted(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    for state in ("queued", "running", "cancel_requested"):
        store.insert(_record(state, state=state))

    restarted = JobStore(path)

    for state in ("queued", "running", "cancel_requested"):
        record = restarted.get(f"job_{state}")
        assert record["state"] == "interrupted"
        assert record["error_code"] == "ADAPTER_RESTARTED"


def test_job_store_rejects_non_plain_database_path(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    path.mkdir()

    with pytest.raises(RuntimeError, match="plain single-link file"):
        JobStore(path)


def test_job_manager_close_is_idempotent_stops_threads_and_releases_lock(
    tmp_path, monkeypatch
):
    manager, _ = _manager(tmp_path, monkeypatch, start_threads=True)
    assert any(thread.is_alive() for thread in manager._threads)
    active = _record("0" * 32, state="running")
    manager.store.insert(active)

    manager.close()
    manager.close()

    assert all(not thread.is_alive() for thread in manager._threads)
    assert manager.store.get(active["job_id"])["state"] == "interrupted"
    assert manager.store.get(active["job_id"])["error_code"] == "ADAPTER_SHUTDOWN"
    replacement = JobManager(manager.config)
    replacement.close()

    strategy, backtest = _request_values()
    with pytest.raises(RuntimeError, match="closed"):
        manager.start(manager.config.dataset("fixture"), strategy, backtest)


def test_concurrent_close_cannot_release_instance_lock_before_owner_stops(
    tmp_path, monkeypatch
):
    manager, _ = _manager(tmp_path, monkeypatch, start_threads=True)
    shutdown_entered = threading.Event()
    allow_shutdown = threading.Event()
    premature_unlock = threading.Event()
    original_interrupt = manager.store.interrupt_active
    original_unlock = manager._instance_lock.close

    def blocking_interrupt(*args, **kwargs):
        if not shutdown_entered.is_set():
            shutdown_entered.set()
            assert allow_shutdown.wait(timeout=5)
        return original_interrupt(*args, **kwargs)

    def observed_unlock():
        if not allow_shutdown.is_set():
            premature_unlock.set()
        return original_unlock()

    monkeypatch.setattr(manager.store, "interrupt_active", blocking_interrupt)
    monkeypatch.setattr(manager._instance_lock, "close", observed_unlock)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.close)
        assert shutdown_entered.wait(timeout=5)
        second_entered = threading.Event()

        def second_close():
            second_entered.set()
            manager.close()

        second = pool.submit(second_close)
        assert second_entered.wait(timeout=5)
        assert not premature_unlock.wait(timeout=0.2)
        with pytest.raises(RuntimeError, match="owns this state_root"):
            JobManager(manager.config)
        allow_shutdown.set()
        first.result(timeout=5)
        second.result(timeout=5)

    replacement = JobManager(manager.config)
    replacement.close()


def test_close_retains_ownership_until_every_worker_has_stopped(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)

    class StuckWorker:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            return None

    stuck = StuckWorker()
    manager._threads.append(stuck)

    with pytest.raises(RuntimeError, match="shutdown is incomplete"):
        manager.close()
    with pytest.raises(RuntimeError, match="owns this state_root"):
        JobManager(manager.config)

    stuck.alive = False
    manager.close()
    replacement = JobManager(manager.config)
    replacement.close()


def test_closed_old_manager_cannot_cancel_new_owner_job(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    manager.close()
    replacement = JobManager(manager.config)
    job_id = "job_" + "c" * 32
    record = _record("c" * 32)
    record["run_id"] = "dsh_20260101T000000Z_0123456789ab"
    replacement.store.insert(record)
    job_dir = replacement.jobs_root / job_id
    job_dir.mkdir()

    try:
        with pytest.raises(RuntimeError, match="job manager is closed"):
            manager.cancel(job_id)
        assert replacement.store.get(job_id)["state"] == "queued"
        assert not (job_dir / "cancel.requested").exists()
    finally:
        replacement.close()


def test_job_store_persists_the_committed_artifact_manifest_digest(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.insert(_record("committed", state="running"))
    manifest_sha256 = "d" * 64

    assert store.get("job_committed")["artifact_manifest_sha256"] is None
    assert store.transition(
        "job_committed",
        ("running",),
        state="succeeded",
        finished_at="2026-01-01T00:00:01Z",
        exit_code=0,
        terminal_status="SUCCESS",
        artifact_manifest_sha256=manifest_sha256,
    )
    assert store.get("job_committed")["artifact_manifest_sha256"] == manifest_sha256

    with pytest.raises(ValueError, match="manifest digest"):
        store.update("job_committed", artifact_manifest_sha256="not-a-digest")


def test_expected_state_transitions_make_start_cancel_race_exclusive(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.insert(_record("race"))
    barrier = threading.Barrier(3)

    def transition(target: str) -> bool:
        barrier.wait()
        return store.transition(
            "job_race",
            ("queued",),
            state=target,
            started_at="2026-01-01T00:00:01Z" if target == "running" else None,
            finished_at="2026-01-01T00:00:01Z" if target == "canceled" else None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(transition, state) for state in ("running", "canceled")]
        barrier.wait()
        outcomes = [future.result() for future in futures]

    assert sorted(outcomes) == [False, True]
    assert store.get("job_race")["state"] in {"running", "canceled"}


def test_atomic_admission_never_exceeds_capacity(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    barrier = threading.Barrier(3)

    def reserve(suffix: str) -> bool:
        barrier.wait()
        _, created = store.reserve(_record(suffix), active_limit=1)
        return created

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, suffix) for suffix in ("one", "two")]
        barrier.wait()
        created = [future.result() for future in futures]

    assert sorted(created) == [False, True]
    assert store.active_count() == 1


def test_submission_id_is_atomic_and_bound_to_one_request(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    first, created = store.reserve(_record("one", submission_id="req_abcdefgh"), active_limit=2)
    assert created is True
    assert first["job_id"] == "job_one"

    repeated, created = store.reserve(_record("two", submission_id="req_abcdefgh"), active_limit=2)
    assert created is False
    assert repeated["job_id"] == "job_one"

    with pytest.raises(ValueError, match="different request"):
        store.reserve(
            _record(
                "three",
                submission_id="req_abcdefgh",
                request_sha256="c" * 64,
            ),
            active_limit=2,
        )


def test_queued_cancel_is_terminal_and_never_launches_worker(tmp_path, monkeypatch):
    manager, dataset = _manager(tmp_path, monkeypatch, max_jobs=1)
    strategy, backtest = _request_values()
    try:
        started = manager.start(
            dataset,
            strategy,
            backtest,
            "req_abcdefgh",
            validation_report_sha256="c" * 64,
            validation_start_date=backtest.start_date,
        )
        assert manager._queue.maxsize == 1
        canceled = manager.cancel(started["job_id"])
        assert canceled["state"] == "canceled"
        assert canceled["cancel_accepted"] is True

        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *args, **kwargs: pytest.fail("a canceled queued job was launched"),
        )
        manager._execute(started["job_id"])
        assert manager.status(started["job_id"])["state"] == "canceled"
    finally:
        manager.close()


def test_manager_retries_return_same_job_without_duplicate_queue_entry(tmp_path, monkeypatch):
    manager, dataset = _manager(tmp_path, monkeypatch)
    strategy, backtest = _request_values()
    arguments = {
        "submission_id": "req_abcdefgh",
        "validation_report_sha256": "c" * 64,
        "validation_start_date": backtest.start_date,
    }
    try:
        first = manager.start(dataset, strategy, backtest, **arguments)
        second = manager.start(dataset, strategy, backtest, **arguments)
        assert second["job_id"] == first["job_id"]
        assert second["run_id"] == first["run_id"]
        assert manager._queue.qsize() == 1

        changed = MaCrossoverSpec(symbol="510300.SH", fast_window=6, slow_window=20)
        with pytest.raises(ValueError, match="different request"):
            manager.start(dataset, changed, backtest, **arguments)
    finally:
        manager.close()


def test_private_request_is_exact_bounded_and_digest_authenticated(tmp_path, monkeypatch):
    manager, dataset = _manager(tmp_path, monkeypatch)
    strategy, backtest = _request_values()
    try:
        started = manager.start(
            dataset,
            strategy,
            backtest,
            "req_abcdefgh",
            validation_report_sha256="c" * 64,
            validation_start_date=backtest.start_date,
        )
        request_path = manager._job_dir(started["job_id"]) / "request.json"
        request = load_private_request(request_path)
        assert request["request_digest"] == started["request_digest"]
        assert request["template_digest"] == template_digest()

        request["resolved_price_mode"] = "raw"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        with pytest.raises(ValueError, match="digest"):
            load_private_request(request_path)
    finally:
        manager.close()


def test_private_json_reader_rejects_path_replacement_between_inspection_and_open(
    tmp_path, monkeypatch
):
    manager, dataset = _manager(tmp_path, monkeypatch)
    strategy, backtest = _request_values()
    try:
        started = manager.start(
            dataset,
            strategy,
            backtest,
            "req_abcdefgh",
            validation_report_sha256="c" * 64,
            validation_start_date=backtest.start_date,
        )
        request_path = manager._job_dir(started["job_id"]) / "request.json"
        replacement = request_path.with_name("replacement.json")
        replacement.write_bytes(request_path.read_bytes())
        original_open = jobs_module.os.open
        replaced = False

        def replacing_open(path, flags, *args):
            nonlocal replaced
            if Path(path) == request_path and not replaced:
                replaced = True
                os.replace(replacement, request_path)
            return original_open(path, flags, *args)

        monkeypatch.setattr(jobs_module.os, "open", replacing_open)

        with pytest.raises(ValueError, match="changed before"):
            load_private_request(request_path)
    finally:
        manager.close()


def test_private_json_reader_rejects_path_replacement_after_open(tmp_path, monkeypatch):
    manager, dataset = _manager(tmp_path, monkeypatch)
    strategy, backtest = _request_values()
    try:
        started = manager.start(
            dataset,
            strategy,
            backtest,
            "req_abcdefgh",
            validation_report_sha256="c" * 64,
            validation_start_date=backtest.start_date,
        )
        request_path = manager._job_dir(started["job_id"]) / "request.json"
        replacement = request_path.with_name("replacement.json")
        replacement.write_bytes(request_path.read_bytes())
        original_lstat = Path.lstat
        inspections = 0

        def replacing_lstat(path):
            nonlocal inspections
            if path == request_path:
                inspections += 1
                if inspections == 2:
                    os.replace(replacement, request_path)
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", replacing_lstat)

        with pytest.raises(ValueError, match="changed while"):
            load_private_request(request_path)
    finally:
        manager.close()


def test_validation_report_preimage_is_frozen_and_digest_authenticated(tmp_path, monkeypatch):
    manager, dataset = _manager(tmp_path, monkeypatch)
    strategy, backtest = _request_values()
    report = _validation_report()
    try:
        started = manager.start(
            dataset,
            strategy,
            backtest,
            "req_report_abcdefgh",
            validation_report_sha256=report["report_sha256"],
            validation_report=report,
            validation_start_date=backtest.start_date,
        )
        report_path = manager._job_dir(started["job_id"]) / "validation-report.json"
        assert load_validation_report(report_path, report["report_sha256"]) == report

        changed = json.loads(report_path.read_text(encoding="utf-8"))
        changed["dataset_kind"] = "substituted"
        report_path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="digest"):
            load_validation_report(report_path, report["report_sha256"])
    finally:
        manager.close()


def test_worker_result_requires_exact_schema_exit_code_and_size(tmp_path, monkeypatch):
    path = tmp_path / "worker-result.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ok": True,
                "output": {
                    "artifact_verified": True,
                    "result_contract": {"status": "SUCCESS"},
                },
            }
        ),
        encoding="utf-8",
    )
    assert _worker_result(path, 0) == ("succeeded", "SUCCESS", None)
    with pytest.raises(ValueError, match="exit code"):
        _worker_result(path, 1)

    monkeypatch.setattr(jobs_module, "MAX_WORKER_RESULT_BYTES", 16)
    with pytest.raises(ValueError, match="size limit"):
        _worker_result(path, 0)


def test_worker_injects_adapter_attribution_into_verified_parameters(tmp_path, monkeypatch):
    strategy, backtest = _request_values()
    validation_sources_sha256 = "d" * 64
    public = build_public_request(
        "fixture",
        strategy,
        backtest,
        resolved_price_mode="dual",
        validation_report_sha256="c" * 64,
        validation_sources_sha256=validation_sources_sha256,
        validation_start_date="20251201",
    )
    private = {
        **public,
        "run_id": "dsh_20260101T000000Z_0123456789ab",
        "data_root": str((tmp_path / "data").resolve()),
        "results_root": str((tmp_path / "results").resolve()),
        "request_digest": request_digest(public),
        "strategy_digest": strategy_digest(strategy),
        "template_digest": template_digest(),
    }
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(private), encoding="utf-8")
    captured = {}

    def fake_run_backtest(*, stop_check=None, **kwargs):
        captured.update(kwargs)
        captured["stop_check"] = stop_check
        return {
            "artifact_verified": True,
            "result_contract": {"status": "SUCCESS"},
        }

    monkeypatch.setattr(worker_module, "run_backtest", fake_run_backtest)
    monkeypatch.setattr(
        worker_module,
        "collect_source_identity",
        lambda *args, **kwargs: ((), validation_sources_sha256),
    )
    assert worker_module.execute(request_path, result_path, tmp_path / "cancel") == 0
    parameters = captured["strategy_params"]
    assert parameters["DIEPI_ADAPTER_REQUEST_SHA256"] == private["request_digest"]
    assert parameters["DIEPI_ADAPTER_STRATEGY_SHA256"] == private["strategy_digest"]
    assert parameters["DIEPI_ADAPTER_TEMPLATE_SHA256"] == template_digest()
    assert parameters["DIEPI_ADAPTER_VERSION"] == __version__
    assert parameters["DIEPI_ADAPTER_VALIDATION_SHA256"] == "c" * 64
    assert parameters["DIEPI_ADAPTER_SOURCES_SHA256"] == validation_sources_sha256
    assert parameters["DIEPI_ADAPTER_VALIDATION_START_DATE"] == "20251201"
    assert callable(captured["stop_check"])


def test_worker_unconditionally_rejects_diepi_without_stop_check(tmp_path, monkeypatch):
    strategy, backtest = _request_values()
    public = build_public_request(
        "fixture",
        strategy,
        backtest,
        resolved_price_mode="dual",
        validation_report_sha256="c" * 64,
        validation_sources_sha256="d" * 64,
        validation_start_date="20251201",
    )
    private = {
        **public,
        "run_id": "dsh_20260101T000000Z_0123456789ab",
        "data_root": str((tmp_path / "data").resolve()),
        "results_root": str((tmp_path / "results").resolve()),
        "request_digest": request_digest(public),
        "strategy_digest": strategy_digest(strategy),
        "template_digest": template_digest(),
    }
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(private), encoding="utf-8")
    data_accessed = False

    def legacy_run_backtest(script_path):
        return script_path

    def unexpected_source_access(*args, **kwargs):
        nonlocal data_accessed
        data_accessed = True
        return (), "d" * 64

    monkeypatch.setattr(worker_module, "run_backtest", legacy_run_backtest)
    monkeypatch.setattr(worker_module, "collect_source_identity", unexpected_source_access)

    assert worker_module.execute(request_path, result_path, tmp_path / "cancel") == 4
    assert data_accessed is False
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["error"]["code"] == "CANCEL_BRIDGE_UNAVAILABLE"
    assert _worker_result(result_path, 4) == (
        "failed",
        None,
        "CANCEL_BRIDGE_UNAVAILABLE",
    )


@pytest.mark.parametrize("operation", ["status", "cancel"])
def test_public_job_entry_rejects_noncanonical_id_before_db_access(
    tmp_path, monkeypatch, operation
):
    manager, _ = _manager(tmp_path, monkeypatch)
    accessed = False

    def unexpected_get(job_id):
        nonlocal accessed
        accessed = True
        raise AssertionError(job_id)

    monkeypatch.setattr(manager.store, "get", unexpected_get)
    try:
        with pytest.raises(ValueError, match=r"job_\[0-9a-f\]\{32\}"):
            getattr(manager, operation)("../job_" + "a" * 32)
        assert accessed is False
    finally:
        manager.close()


def test_public_status_fails_closed_on_tampered_db_text():
    record = {
        "job_id": "job_" + "a" * 32,
        "run_id": "dsh_20260101T000000Z_0123456789ab",
        "dataset_id": "fixture",
        "state": "failed",
        "request_digest": "a" * 64,
        "strategy_digest": "b" * 64,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": None,
        "finished_at": "2026-01-01T00:00:01Z",
        "exit_code": 1,
        "terminal_status": None,
        "error_code": "BAD\nC:\\private\\secret",
        "submission_id": "req_abcdefgh",
        "artifact_manifest_sha256": None,
    }

    with pytest.raises(RuntimeError, match="adapter job state is invalid"):
        JobManager.public_status(record)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "queued", "finished_at": "2026-01-01T00:00:01Z"},
        {"state": "running", "started_at": None},
        {
            "state": "succeeded",
            "started_at": "2026-01-01T00:00:01Z",
            "finished_at": "2026-01-01T00:00:02Z",
            "exit_code": 0,
            "terminal_status": "SUCCESS",
            "artifact_manifest_sha256": None,
        },
        {
            "state": "canceled",
            "finished_at": "2026-01-01T00:00:02Z",
            "terminal_status": "CANCELED",
        },
        {
            "state": "interrupted",
            "finished_at": "2026-01-01T00:00:02Z",
            "error_code": None,
        },
        {
            "state": "failed",
            "finished_at": "2026-01-01T00:00:02Z",
            "error_code": None,
        },
        {"started_at": "2025-12-31T23:59:59Z"},
    ],
)
def test_public_status_rejects_impossible_state_field_combinations(changes):
    record = {
        "job_id": "job_" + "a" * 32,
        "run_id": "dsh_20260101T000000Z_0123456789ab",
        "dataset_id": "fixture",
        "state": "running",
        "request_digest": "a" * 64,
        "strategy_digest": "b" * 64,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": "2026-01-01T00:00:01Z",
        "finished_at": None,
        "exit_code": None,
        "terminal_status": None,
        "error_code": None,
        "submission_id": "req_abcdefgh",
        "artifact_manifest_sha256": None,
    }
    record.update(changes)

    with pytest.raises(RuntimeError, match="adapter job state is invalid"):
        JobManager.public_status(record)


def test_worker_fails_closed_when_validated_data_generation_changes(tmp_path, monkeypatch):
    strategy, backtest = _request_values()
    validated_sources_sha256 = "d" * 64
    observed_sources_sha256 = "e" * 64
    public = build_public_request(
        "fixture",
        strategy,
        backtest,
        resolved_price_mode="dual",
        validation_report_sha256="c" * 64,
        validation_sources_sha256=validated_sources_sha256,
        validation_start_date="20251201",
    )
    private = {
        **public,
        "run_id": "dsh_20260101T000000Z_0123456789ab",
        "data_root": str((tmp_path / "data").resolve()),
        "results_root": str((tmp_path / "results").resolve()),
        "request_digest": request_digest(public),
        "strategy_digest": strategy_digest(strategy),
        "template_digest": template_digest(),
    }
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(private), encoding="utf-8")
    source_calls = []
    run_called = False

    def fake_collect_source_identity(data_root, **kwargs):
        source_calls.append({"data_root": data_root, **kwargs})
        return (), observed_sources_sha256

    def fake_run_backtest(*, stop_check=None, **kwargs):
        nonlocal run_called
        run_called = True
        return {"stop_check": stop_check, **kwargs}

    monkeypatch.setattr(
        worker_module,
        "collect_source_identity",
        fake_collect_source_identity,
    )
    monkeypatch.setattr(worker_module, "run_backtest", fake_run_backtest)

    assert worker_module.execute(request_path, result_path, tmp_path / "cancel") == 1
    assert run_called is False
    assert source_calls == [
        {
            "data_root": Path(private["data_root"]),
            "symbol": strategy.symbol,
            "price_mode": "dual",
            "start_date": backtest.start_date,
            "end_date": backtest.end_date,
        }
    ]
    worker_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert worker_result["ok"] is False
    assert worker_result["error"]["code"] == "DATA_SOURCE_IDENTITY_CHANGED"


def test_worker_rejects_validation_start_after_backtest_start(tmp_path, monkeypatch):
    strategy, backtest = _request_values()
    public = build_public_request(
        "fixture",
        strategy,
        backtest,
        resolved_price_mode="dual",
        validation_report_sha256="c" * 64,
        validation_start_date="20260201",
    )
    private = {
        **public,
        "run_id": "dsh_20260101T000000Z_0123456789ab",
        "data_root": str((tmp_path / "data").resolve()),
        "results_root": str((tmp_path / "results").resolve()),
        "request_digest": request_digest(public),
        "strategy_digest": strategy_digest(strategy),
        "template_digest": template_digest(),
    }
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(json.dumps(private), encoding="utf-8")
    called = False

    def fake_run_backtest(*, stop_check=None, **kwargs):
        nonlocal called
        called = True
        return {"stop_check": stop_check, **kwargs}

    monkeypatch.setattr(worker_module, "run_backtest", fake_run_backtest)
    assert worker_module.execute(request_path, result_path, tmp_path / "cancel") == 1
    assert called is False


def test_child_environment_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", "system-root")
    monkeypatch.setenv("TEMP", "temporary")
    monkeypatch.setenv("PATH", "poison-path")
    monkeypatch.setenv("PYTHONPATH", "poison-import")
    monkeypatch.setenv("BROKER_TOKEN", "secret")
    manager = JobManager.__new__(JobManager)

    child = manager._child_env()

    normalized = {key.upper(): value for key, value in child.items()}
    assert normalized["SYSTEMROOT"] == "system-root"
    assert normalized["TEMP"] == "temporary"
    assert "PATH" not in normalized
    assert "PYTHONPATH" not in normalized
    assert "BROKER_TOKEN" not in normalized


def test_isolated_worker_module_launches_with_allowlisted_environment(tmp_path, monkeypatch):
    manager, dataset = _manager(tmp_path, monkeypatch)
    strategy, backtest = _request_values()
    try:
        started = manager.start(
            dataset,
            strategy,
            backtest,
            "req_abcdefgh",
            validation_report_sha256="c" * 64,
            validation_start_date=backtest.start_date,
        )
        manager._execute(started["job_id"])
        status = manager.status(started["job_id"])
        assert status["state"] == "failed"
        assert status["error_code"] not in {
            "ADAPTER_WORKER_FAILED",
            "WORKER_RESULT_INVALID",
        }
    finally:
        manager.close()


@pytest.mark.parametrize(
    ("cancel_first", "expected_reason"),
    [(False, "JOB_TIMEOUT"), (True, "CANCEL_FORCE_TERMINATED")],
)
def test_process_monitor_enforces_timeout_and_cancel_grace(
    tmp_path, monkeypatch, cancel_first, expected_reason
):
    monkeypatch.setattr(jobs_module, "JOB_TIMEOUT_SECONDS", 2.0 if cancel_first else 0.05)
    monkeypatch.setattr(jobs_module, "CANCEL_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(jobs_module, "TERMINATE_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(jobs_module, "PROCESS_POLL_SECONDS", 0.01)
    cancel_file = tmp_path / "cancel.requested"
    if cancel_first:
        cancel_file.touch()
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"},
    )
    manager = JobManager.__new__(JobManager)
    try:
        _, reason = manager._wait_for_process(process, cancel_file)
        assert reason == expected_reason
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
