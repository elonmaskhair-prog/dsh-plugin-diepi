import json

import pytest

from diepi_mcp.config import load_config


def _write_config(tmp_path, **dataset_overrides):
    data = tmp_path / "data"
    data.mkdir()
    dataset = {
        "description": "fixture",
        "data_root": "data",
        "results_root": "results",
        "data_grade": "raw_only",
        "default_price_mode": "raw",
        **dataset_overrides,
    }
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state_root": "state",
                "datasets": {"fixture": dataset},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_config_resolves_paths_but_public_dataset_hides_them(tmp_path):
    config = load_config(_write_config(tmp_path))
    dataset = config.dataset("fixture")
    assert dataset.data_root == (tmp_path / "data").resolve()
    assert "root" not in " ".join(dataset.public_dict())
    assert dataset.public_dict()["data_grade"] == "raw_only"


def test_config_accepts_utf8_bom_from_windows_powershell(tmp_path):
    path = _write_config(tmp_path)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    config = load_config(path)

    assert config.dataset("fixture").data_root == (tmp_path / "data").resolve()


def test_config_rejects_grade_price_mode_mismatch(tmp_path):
    path = _write_config(tmp_path, default_price_mode="dual")
    with pytest.raises(ValueError, match="requires default_price_mode=raw"):
        load_config(path)


def test_config_rejects_results_inside_data_root(tmp_path):
    path = _write_config(tmp_path, results_root="data/results")
    with pytest.raises(ValueError, match="results_root"):
        load_config(path)


def test_config_rejects_cross_dataset_writable_root_inside_other_data(tmp_path):
    first_data = tmp_path / "first-data"
    second_data = tmp_path / "second-data"
    first_data.mkdir()
    second_data.mkdir()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state_root": "state",
                "datasets": {
                    "first": {
                        "data_root": "first-data",
                        "results_root": "second-data/results",
                        "data_grade": "raw_only",
                        "default_price_mode": "raw",
                    },
                    "second": {
                        "data_root": "second-data",
                        "results_root": "second-results",
                        "data_grade": "raw_only",
                        "default_price_mode": "raw",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="second.data_root"):
        load_config(path)


def test_config_rejects_overlapping_writable_roots(tmp_path):
    path = _write_config(tmp_path, results_root="state/results")
    with pytest.raises(ValueError, match="must not overlap"):
        load_config(path)
