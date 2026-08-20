from hashlib import sha256
from types import SimpleNamespace

import pandas as pd
import pytest

import diepi_mcp.service as service_module
from diepi_mcp.config import DatasetConfig
from diepi_mcp.models import BacktestSpec, MaCrossoverSpec
from diepi_mcp.service import QuantService
from diepi_mcp.strategy import canonical_json


_SOURCE_IDENTITY_SHA256 = "c" * 64


class _ValidationReport:
    contract_ready = True
    report_sha256 = "a" * 64

    def to_dict(self):
        return {
            "contract_ready": self.contract_ready,
            "report_sha256": self.report_sha256,
        }


class _Jobs:
    def __init__(self):
        self.calls = []

    def start(
        self,
        dataset,
        strategy,
        backtest,
        submission_id,
        **validation_evidence,
    ):
        self.calls.append(
            {
                "dataset": dataset,
                "strategy": strategy,
                "backtest": backtest,
                "submission_id": submission_id,
                **validation_evidence,
            }
        )
        return {
            "job_id": "job_test",
            "request_digest": "b" * 64,
            "state": "queued",
        }


def _dataset(tmp_path):
    return DatasetConfig(
        dataset_id="fixture",
        description="",
        data_root=tmp_path / "data",
        results_root=tmp_path / "results",
        data_grade="dual",
        default_price_mode="dual",
    )


def _service(tmp_path):
    service = object.__new__(QuantService)
    dataset = _dataset(tmp_path)
    service.config = SimpleNamespace(
        max_calendar_days=10_000,
        dataset=lambda dataset_id: (
            dataset
            if dataset_id == dataset.dataset_id
            else (_ for _ in ()).throw(ValueError("unknown dataset_id"))
        ),
    )
    service.jobs = _Jobs()
    return service, dataset


def test_capabilities_are_semantically_accurate_and_sanitize_dataset_description(
    tmp_path,
):
    service = object.__new__(QuantService)
    dataset = DatasetConfig(
        dataset_id="fixture",
        description=f"host data at {tmp_path.resolve()}\nprivate",
        data_root=tmp_path / "data",
        results_root=tmp_path / "results",
        data_grade="raw_only",
        default_price_mode="raw",
    )
    service.config = SimpleNamespace(datasets={dataset.dataset_id: dataset})

    capabilities = service.capabilities()

    public_dataset = capabilities["datasets"][0]
    assert "\n" not in public_dataset["description"]
    assert str(tmp_path.resolve()) not in public_dataset["description"]
    assert "<path>" in public_dataset["description"]
    admission = capabilities["data_contract"]["admission"]
    assert "exact-scope validation" in admission
    assert "manifest is verified when present or required" in admission
    assert "strict manifest-backed" not in admission


def _provider_class(*, observed=21, validation_start="20231201"):
    class FakeProvider:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.pair_calls = []
            self.__class__.instances.append(self)

        @staticmethod
        def get_trade_days_between(start, end):
            assert (start, end) == ("20240101", "20240329")
            return ["20240102", "20240103"]

        @staticmethod
        def get_prev_trade_day(date, n=1):
            assert date == "20240102"
            assert n == 1
            return "20231229" if validation_start is not None else None

        def get_aligned_pair(self, symbol, **kwargs):
            self.pair_calls.append((symbol, kwargs))
            return SimpleNamespace(
                strategy=pd.DataFrame(
                    index=pd.date_range(validation_start, periods=observed, freq="D")
                )
            )

    return FakeProvider


def _patch_validation(monkeypatch):
    calls = []

    def fake_validate_local_data(**kwargs):
        calls.append(kwargs)
        return _ValidationReport()

    monkeypatch.setattr(service_module, "validate_local_data", fake_validate_local_data)
    return calls


def _patch_source_identity(
    monkeypatch,
    digests=(_SOURCE_IDENTITY_SHA256, _SOURCE_IDENTITY_SHA256),
):
    calls = []

    def fake_collect_source_identity(data_root, **kwargs):
        calls.append({"data_root": data_root, **kwargs})
        return (), digests[len(calls) - 1]

    monkeypatch.setattr(
        service_module,
        "collect_source_identity",
        fake_collect_source_identity,
    )
    return calls


def test_start_backtest_validates_and_binds_the_full_warmup_scope(monkeypatch, tmp_path):
    service, dataset = _service(tmp_path)
    provider_class = _provider_class()
    monkeypatch.setattr(service_module, "DataProvider", provider_class)
    validation_calls = _patch_validation(monkeypatch)
    source_identity_calls = _patch_source_identity(monkeypatch)
    backtest = BacktestSpec(start_date="20240101", end_date="20240329")

    response = service.start_backtest(
        "fixture",
        MaCrossoverSpec(symbol="510300.SH", fast_window=5, slow_window=20),
        backtest,
        "req_warmup_scope_0001",
    )

    assert response["accepted"] is True
    assert response["submission_id"] == "req_warmup_scope_0001"
    assert validation_calls == [
        {
            "data_root": dataset.data_root,
            "symbols": ("510300.SH",),
            "start_date": "20231201",
            "end_date": "20240329",
            "frequency": "daily",
            "price_mode": "dual",
            "verify_manifest": True,
        }
    ]
    assert len(provider_class.instances) == 2
    for provider in provider_class.instances:
        assert provider.kwargs == {
            "data_root": dataset.data_root,
            "price_mode": "hfq",
            "execution_price_mode": "raw",
        }
        assert provider.pair_calls == [
            (
                "510300.SH",
                {
                    "frequency": "daily",
                    "end": "20231229",
                    "count": 21,
                },
            )
        ]
    assert len(service.jobs.calls) == 1
    queued = service.jobs.calls[0]
    assert queued["backtest"].start_date == "20240101"
    assert queued["submission_id"] == "req_warmup_scope_0001"
    assert queued["validation_report_sha256"] == "a" * 64
    assert queued["validation_report"] == _ValidationReport().to_dict()
    assert queued["validation_sources_sha256"] == _SOURCE_IDENTITY_SHA256
    assert queued["validation_start_date"] == "20231201"
    expected_source_identity_call = {
        "data_root": dataset.data_root,
        "symbol": "510300.SH",
        "price_mode": "dual",
        "start_date": "20240101",
        "end_date": "20240329",
    }
    assert source_identity_calls == [
        expected_source_identity_call,
        expected_source_identity_call,
    ]
    assert response["validation_sources_sha256"] == _SOURCE_IDENTITY_SHA256
    assert response["validation_scope"] == {
        "requested_start_date": "20240101",
        "validation_start_date": "20231201",
        "validation_end_date": "20240329",
        "first_backtest_trade_date": "20240102",
        "warmup_end_date": "20231229",
        "required_warmup_bars": 21,
        "observed_warmup_bars": 21,
    }
    binding = response["validation_binding"]
    assert binding["validation_sources_sha256"] == _SOURCE_IDENTITY_SHA256
    payload = {key: value for key, value in binding.items() if key != "binding_sha256"}
    assert binding["binding_sha256"] == sha256(canonical_json(payload)).hexdigest()


def test_start_backtest_rejects_market_data_changed_during_validation(monkeypatch, tmp_path):
    service, _ = _service(tmp_path)
    monkeypatch.setattr(service_module, "DataProvider", _provider_class())
    validation_calls = _patch_validation(monkeypatch)
    source_identity_calls = _patch_source_identity(
        monkeypatch,
        digests=("c" * 64, "d" * 64),
    )

    response = service.start_backtest(
        "fixture",
        MaCrossoverSpec(symbol="510300.SH", fast_window=5, slow_window=20),
        BacktestSpec(start_date="20240101", end_date="20240329"),
        "req_data_changed_0001",
    )

    assert response["accepted"] is False
    assert response["code"] == "DATA_CHANGED_DURING_VALIDATION"
    assert len(validation_calls) == 1
    assert len(source_identity_calls) == 2
    assert service.jobs.calls == []


def test_start_backtest_rejects_insufficient_symbol_warmup(monkeypatch, tmp_path):
    service, _ = _service(tmp_path)
    monkeypatch.setattr(service_module, "DataProvider", _provider_class(observed=20))
    _patch_source_identity(monkeypatch)
    _patch_validation(monkeypatch)

    response = service.start_backtest(
        "fixture",
        MaCrossoverSpec(symbol="510300.SH", fast_window=5, slow_window=20),
        BacktestSpec(start_date="20240101", end_date="20240329"),
        "req_warmup_short_0001",
    )

    assert response["accepted"] is False
    assert response["code"] == "WARMUP_HISTORY_INSUFFICIENT"
    assert "requires 21 completed pre-start bars" in response["message"]
    assert "only 20 are available" in response["message"]
    assert service.jobs.calls == []


def test_start_backtest_rejects_submission_id_before_data_access(monkeypatch, tmp_path):
    service, _ = _service(tmp_path)
    provider_called = False

    class UnexpectedProvider:
        def __init__(self, **kwargs):
            nonlocal provider_called
            provider_called = True

    monkeypatch.setattr(service_module, "DataProvider", UnexpectedProvider)
    with pytest.raises(ValueError, match="submission_id"):
        service.start_backtest(
            "fixture",
            MaCrossoverSpec(symbol="510300.SH", fast_window=5, slow_window=20),
            BacktestSpec(start_date="20240101", end_date="20240329"),
            "not-valid",
        )
    assert provider_called is False


def test_start_backtest_rejects_insufficient_calendar_warmup(monkeypatch, tmp_path):
    service, _ = _service(tmp_path)
    monkeypatch.setattr(
        service_module,
        "DataProvider",
        _provider_class(validation_start=None),
    )
    validation_calls = _patch_validation(monkeypatch)
    _patch_source_identity(monkeypatch)

    response = service.start_backtest(
        "fixture",
        MaCrossoverSpec(symbol="510300.SH", fast_window=5, slow_window=20),
        BacktestSpec(start_date="20240101", end_date="20240329"),
        "req_warmup_calendar_0001",
    )

    assert response["accepted"] is False
    assert response["code"] == "WARMUP_CALENDAR_INSUFFICIENT"
    assert validation_calls == []
    assert service.jobs.calls == []


def test_validate_data_keeps_the_callers_requested_interval(monkeypatch, tmp_path):
    service, dataset = _service(tmp_path)
    validation_calls = _patch_validation(monkeypatch)

    response = service.validate_data(
        "fixture",
        ["510300.SH"],
        "20240101",
        "20240329",
    )

    assert response["contract_ready"] is True
    assert validation_calls[0]["data_root"] == dataset.data_root
    assert validation_calls[0]["start_date"] == "20240101"
    assert validation_calls[0]["end_date"] == "20240329"


def test_validate_data_returns_a_path_private_bounded_projection(monkeypatch, tmp_path):
    service, _ = _service(tmp_path)

    class UnsafeReport:
        def to_dict(self):
            return {
                "schema_version": 1,
                "status": "fail",
                "contract_ready": False,
                "scope": {
                    "symbols": ["510300.SH"],
                    "start_date": "20240101",
                    "end_date": "20240329",
                    "frequency": "daily",
                    "price_mode": "dual",
                },
                "dataset_kind": "user_supplied",
                "manifest_status": "failed",
                "manifest_sha256": None,
                "calendar": {
                    "status": "fail",
                    "source": "C:\\private\\calendar\nsecret",
                    "calendar_id": None,
                    "version": None,
                    "content_sha256": None,
                    "rows": 0,
                    "first_date": None,
                    "last_date": None,
                    "open_days_in_scope": 0,
                },
                "pair_reports": [
                    {
                        "status": "fail",
                        "symbol": "510300.SH",
                        "strategy_rows": 1,
                        "execution_rows": 1,
                        "aligned_rows": 1,
                        "issues": [
                            {
                                "code": "BAD_BAR",
                                "message": "C:\\private\\pair\nsecret",
                                "sample_keys": ["attacker-controlled"],
                            }
                        ],
                    }
                ],
                "issues": [
                    {
                        "severity": "error",
                        "code": "READ_ERROR",
                        "message": "C:\\private\\file.parquet\nsecret",
                        "symbol": "510300.SH",
                        "sample_keys": ["attacker-controlled"],
                    }
                ],
                "limitations": ["unsafe"],
                "report_sha256": "f" * 64,
            }

    monkeypatch.setattr(service_module, "validate_local_data", lambda **kwargs: UnsafeReport())

    response = service.validate_data(
        "fixture",
        ["510300.SH"],
        "20240101",
        "20240329",
    )
    serialized = str(response)
    assert response["projection"] == "diepi_mcp.validation_public_v1"
    assert response["issues"] == [
        {"code": "READ_ERROR", "severity": "error", "symbol": "510300.SH"}
    ]
    assert response["pair_reports"][0]["issue_codes"] == ["BAD_BAR"]
    assert "report_sha256" not in response
    assert "message" not in serialized
    assert "sample_keys" not in serialized
    assert "private" not in serialized
    assert "secret" not in serialized


def test_service_startup_refuses_diepi_without_stop_check(monkeypatch):
    def legacy_run_backtest(script_path):
        return script_path

    monkeypatch.setattr(service_module, "run_backtest", legacy_run_backtest)

    with pytest.raises(RuntimeError, match="stop_check"):
        QuantService(SimpleNamespace())
