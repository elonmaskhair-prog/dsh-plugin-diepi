import re
from pathlib import Path

from diepi_mcp import __version__


def test_runtime_version_matches_project_metadata():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, flags=re.MULTILINE)

    assert match is not None
    assert __version__ == match.group(1)
