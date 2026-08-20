from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from diepi_mcp.security import (
    confined_child,
    raise_public_tool_error,
    sanitize_public_text,
)


def test_public_text_is_single_line_path_private_and_bounded():
    value = "bad\x00\nC:\\Users\\alice\\private file.json and /srv/private/data " + "x" * 1000

    sanitized = sanitize_public_text(value)

    assert "\x00" not in sanitized
    assert "\n" not in sanitized
    assert "alice" not in sanitized
    assert "/srv" not in sanitized
    assert "<path>" in sanitized
    assert len(sanitized) <= 240


@pytest.mark.parametrize(
    "path_text",
    [
        "/srv/private user/secret/file.csv",
        "/srv/private/report final.csv",
        "/srv/private\N{NO-BREAK SPACE}user/secret/file.csv",
        "/srv/private\N{EM SPACE}user/secret/file.csv",
    ],
)
def test_public_text_redacts_unquoted_spaced_posix_path_through_line(path_text):
    sanitized = sanitize_public_text(f"ordinary text before {path_text} ordinary text after")

    assert sanitized == "ordinary text before <path>"
    assert all(
        fragment not in sanitized
        for fragment in ("/srv", "private", "secret", "report", "final")
    )


@pytest.mark.parametrize(
    "path_text",
    [
        '"/srv/private user/secret/file.csv"',
        "\N{LEFT DOUBLE QUOTATION MARK}/srv/private user/secret/file.csv"
        "\N{RIGHT DOUBLE QUOTATION MARK}",
    ],
)
def test_public_text_redacts_quoted_posix_path_and_preserves_following_text(path_text):
    sanitized = sanitize_public_text(f"ordinary before {path_text} ordinary after")

    assert sanitized == "ordinary before <path> ordinary after"


def test_public_text_preserves_text_after_explicit_posix_path_delimiter():
    sanitized = sanitize_public_text("cannot read /srv/private/report final.csv; retry later")

    assert sanitized == "cannot read <path>; retry later"


@pytest.mark.parametrize(
    "path_text",
    [r"C:\Users\alice\private file.csv", r"\\server\private share\file.csv"],
)
def test_public_text_still_redacts_windows_and_unc_paths(path_text):
    sanitized = sanitize_public_text(f"cannot read {path_text}")

    assert "<path>" in sanitized
    assert "alice" not in sanitized
    assert "server" not in sanitized
    assert "private" not in sanitized


def test_public_exception_mapping_does_not_chain_or_expose_paths():
    with pytest.raises(RuntimeError) as raised:
        raise_public_tool_error(ValueError("cannot read C:\\private\\secret\n" + "x" * 1000))

    assert raised.value.__cause__ is None
    assert "DIEPI_MCP_INVALID_ARGUMENT" in str(raised.value)
    assert "private" not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert len(str(raised.value)) < 300


def test_confined_child_rejects_reparse_in_trusted_root(tmp_path, monkeypatch):
    root = tmp_path / "results"
    root.mkdir()
    original_lstat = Path.lstat

    def reparse_lstat(path):
        metadata = original_lstat(path)
        if path == root:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(ValueError, match="plain directory components"):
        confined_child(root, "job_" + "a" * 32, "result")


def test_confined_child_rejects_dangling_link_entry(tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    link = root / ("job_" + "a" * 32)
    try:
        link.symlink_to(root / "missing-target", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="symbolic link"):
        confined_child(root, link.name, "result")
