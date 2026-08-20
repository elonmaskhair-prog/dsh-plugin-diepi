import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

import diepi_mcp.bootstrap as bootstrap_module
from diepi_mcp.bootstrap import CONFIG_FILENAME, initialize_synthetic_home
from diepi_mcp.config import load_config
from diepi_mcp.server import main


def test_init_config_creates_only_a_deterministic_synthetic_home(tmp_path, capsys):
    target = tmp_path / "adapter-home"

    main(["init-config", "--target", str(target)])

    output = json.loads(capsys.readouterr().out)
    config = load_config(target / CONFIG_FILENAME)
    dataset = config.dataset("synthetic-demo")
    manifest = json.loads((dataset.data_root / "diepi_dataset.json").read_text(encoding="utf-8"))
    assert output["ok"] is True
    assert output["synthetic"] is True
    assert output["symbol"] == "000001.SZ"
    assert manifest["dataset_kind"] == "synthetic_demo"
    assert manifest["generator"] == "diepi.demo.generator"
    assert dataset.data_root == (target / "synthetic-demo" / "market-data").resolve()
    assert dataset.results_root == (target / "results").resolve()
    assert config.state_root == (target / "state").resolve()
    assert (target / "results").is_dir()
    assert (target / "state").is_dir()
    assert not (target / bootstrap_module._INCOMPLETE_FILENAME).exists()

    raw_config = (target / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert str(tmp_path) not in raw_config
    with pytest.raises(FileExistsError, match="already exists"):
        initialize_synthetic_home(target)


def test_init_config_cleans_only_its_private_staging_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "adapter-home"

    def fail_generation(workspace):
        raise RuntimeError("synthetic generation failed")

    monkeypatch.setattr(bootstrap_module, "generate_synthetic_demo", fail_generation)
    with pytest.raises(RuntimeError, match="synthetic generation failed"):
        initialize_synthetic_home(target)

    assert target.is_dir()
    assert (target / bootstrap_module._INCOMPLETE_FILENAME).is_file()
    assert not (target / CONFIG_FILENAME).exists()
    assert list(tmp_path.glob(".diepi-mcp-init-*")) == []


@pytest.mark.parametrize("raced_kind", ["directory", "file", "symlink"])
def test_init_config_never_replaces_a_raced_destination(
    tmp_path, monkeypatch, raced_kind
):
    target = tmp_path / "adapter-home"
    victim = tmp_path / "victim"
    victim.mkdir()
    sentinel = victim / "sentinel.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    original_claim = bootstrap_module._claim_destination_exclusive

    def race_then_claim(destination):
        if raced_kind == "directory":
            destination.mkdir()
        elif raced_kind == "file":
            destination.write_text("competitor", encoding="utf-8")
        else:
            try:
                destination.symlink_to(victim, target_is_directory=True)
            except OSError:
                # Windows may deny symlink creation without Developer Mode.
                # A same-volume hard link still exercises a real, portable link.
                destination.hardlink_to(sentinel)
        return original_claim(destination)

    monkeypatch.setattr(bootstrap_module, "_claim_destination_exclusive", race_then_claim)
    with pytest.raises(FileExistsError, match="already exists"):
        initialize_synthetic_home(target)

    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    if raced_kind == "directory":
        assert target.is_dir()
        assert list(target.iterdir()) == []
    elif raced_kind == "file":
        assert target.read_text(encoding="utf-8") == "competitor"
    else:
        assert target.is_symlink() or target.samefile(sentinel)
    assert list(tmp_path.glob(".diepi-mcp-init-*")) == []


def test_init_config_publishes_config_only_after_payload_is_complete(
    tmp_path, monkeypatch
):
    target = tmp_path / "adapter-home"
    original_write = bootstrap_module._write_text_exclusive

    def fail_config_publish(path, content):
        if path.name == CONFIG_FILENAME:
            assert (target / "synthetic-demo" / "market-data").is_dir()
            assert (target / "state").is_dir()
            assert (target / "results").is_dir()
            assert (target / "README.txt").is_file()
            raise OSError("config commit failed")
        return original_write(path, content)

    monkeypatch.setattr(bootstrap_module, "_write_text_exclusive", fail_config_publish)
    with pytest.raises(OSError, match="config commit failed"):
        initialize_synthetic_home(target)

    assert not (target / CONFIG_FILENAME).exists()
    assert (target / bootstrap_module._INCOMPLETE_FILENAME).is_file()
    assert list(tmp_path.glob(".diepi-mcp-init-*")) == []


def test_init_config_rejects_reparse_parent_before_writing(tmp_path, monkeypatch):
    parent = tmp_path / "host-home"
    parent.mkdir()
    original_lstat = Path.lstat

    def reparse_lstat(path):
        metadata = original_lstat(path)
        if path == parent:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(ValueError, match="plain directories"):
        initialize_synthetic_home(parent / "adapter-home")

    assert list(parent.iterdir()) == []


def test_init_config_rejects_symlink_parent_before_writing(tmp_path, monkeypatch):
    parent = tmp_path / "host-home"
    parent.mkdir()
    original_is_symlink = Path.is_symlink

    def symlink_parent(path):
        return path == parent or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", symlink_parent)
    with pytest.raises(ValueError, match="plain directories"):
        initialize_synthetic_home(parent / "adapter-home")

    assert list(parent.iterdir()) == []
