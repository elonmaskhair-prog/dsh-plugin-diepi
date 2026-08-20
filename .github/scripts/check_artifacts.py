"""Inspect release-candidate payloads without installing them."""

from __future__ import annotations

import json
from pathlib import Path
from pathlib import PurePosixPath
import re
import sys
import tarfile
import zipfile


FORBIDDEN_PARTS = (
    ".agents/",
    ".deepeval/",
    ".diepi-mcp-state/",
    ".dsh-experience/",
    ".dsh-smoke",
    "api.md",
    "config.local.json",
)
FORBIDDEN_SUFFIXES = (".parquet", ".sqlite", ".sqlite3", ".db")
FORBIDDEN_SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
MAX_MEMBER_BYTES = 8 * 1024 * 1024
SECRET_PATTERNS = (
    (
        "private-key PEM",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("AWS access key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("provider token", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "assigned credential",
        re.compile(
            rb"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|"
            rb"tushare[_-]?token|deepseek[_-]?api[_-]?key)\s*[:=]\s*"
            rb"[\"'][^\"'\r\n]{12,}[\"']"
        ),
    ),
)
EXPECTED_PYTHON_MODULES = {
    "diepi_mcp/__init__.py",
    "diepi_mcp/__main__.py",
    "diepi_mcp/artifact_binding.py",
    "diepi_mcp/bootstrap.py",
    "diepi_mcp/config.py",
    "diepi_mcp/integration.py",
    "diepi_mcp/jobs.py",
    "diepi_mcp/models.py",
    "diepi_mcp/results.py",
    "diepi_mcp/security.py",
    "diepi_mcp/server.py",
    "diepi_mcp/service.py",
    "diepi_mcp/source_binding.py",
    "diepi_mcp/strategies/__init__.py",
    "diepi_mcp/strategies/ma_crossover.py",
    "diepi_mcp/strategy.py",
    "diepi_mcp/worker.py",
}


def _normalized(names: list[str]) -> list[str]:
    return [name.replace("\\", "/").lower() for name in names]


def _assert_clean(names: list[str], label: str) -> None:
    normalized = _normalized(names)
    for name in normalized:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise AssertionError(f"{label} contains unsafe archive path: {name}")
        if any(part in name for part in FORBIDDEN_PARTS):
            raise AssertionError(f"{label} contains forbidden path: {name}")
        if name.endswith(FORBIDDEN_SUFFIXES):
            raise AssertionError(f"{label} contains market/state payload: {name}")
        basename = path.name
        if basename == ".env" or basename.startswith(".env."):
            raise AssertionError(f"{label} contains an environment file: {name}")
        if name.endswith(FORBIDDEN_SECRET_SUFFIXES):
            raise AssertionError(f"{label} contains a credential-shaped file: {name}")


def _assert_no_secrets(data: bytes, label: str, name: str) -> None:
    if len(data) > MAX_MEMBER_BYTES:
        raise AssertionError(f"{label} contains oversized release member: {name}")
    for secret_label, pattern in SECRET_PATTERNS:
        if pattern.search(data) is not None:
            raise AssertionError(f"{label} contains {secret_label}: {name}")


def _assert_wheel_modules(names: list[str]) -> None:
    modules = {name.replace("\\", "/") for name in names if name.endswith(".py")}
    if modules != EXPECTED_PYTHON_MODULES:
        raise AssertionError(
            "unexpected wheel Python modules:\n"
            + "\n".join(sorted(modules ^ EXPECTED_PYTHON_MODULES))
        )


def _assert_sdist_modules(names: list[str]) -> None:
    modules: set[str] = set()
    marker = "/src/diepi_mcp/"
    for raw_name in names:
        name = raw_name.replace("\\", "/")
        if not name.endswith(".py") or marker not in name:
            continue
        modules.add("diepi_mcp/" + name.split(marker, 1)[1])
    if modules != EXPECTED_PYTHON_MODULES:
        raise AssertionError(
            "unexpected sdist package modules:\n"
            + "\n".join(sorted(modules ^ EXPECTED_PYTHON_MODULES))
        )


def _check_python(dist: Path) -> None:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise AssertionError("expected exactly one wheel and one sdist")

    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        _assert_clean(names, "wheel")
        _assert_wheel_modules(names)
        for member in archive.infolist():
            if member.is_dir():
                continue
            if member.file_size > MAX_MEMBER_BYTES:
                raise AssertionError(f"wheel contains oversized release member: {member.filename}")
            _assert_no_secrets(archive.read(member), "wheel", member.filename)
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
        metadata_lines = set(metadata.splitlines())
        if "Version: 0.1.0a1" not in metadata_lines:
            raise AssertionError("wheel version is not 0.1.0a1")
        if "Requires-Dist: diepi==0.1.1" not in metadata_lines:
            raise AssertionError("wheel does not pin diepi==0.1.1")
        if "Requires-Dist: mcp==1.29.0" not in metadata_lines:
            raise AssertionError("wheel does not pin mcp==1.29.0")
        if "Requires-Dist: pydantic-settings<2.15,>=2.12" not in metadata_lines:
            raise AssertionError("wheel has the wrong pydantic-settings guard")
        if not any(name.endswith("/licenses/THIRD_PARTY_NOTICES.md") for name in names):
            raise AssertionError("wheel is missing THIRD_PARTY_NOTICES.md")

    with tarfile.open(sdists[0], "r:gz") as archive:
        names = archive.getnames()
        _assert_clean(names, "sdist")
        _assert_sdist_modules(names)
        for member in archive.getmembers():
            if not member.isfile():
                continue
            if member.size > MAX_MEMBER_BYTES:
                raise AssertionError(f"sdist contains oversized release member: {member.name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise AssertionError(f"sdist member could not be read: {member.name}")
            _assert_no_secrets(extracted.read(), "sdist", member.name)


def _check_npm(tgz: Path) -> None:
    with tarfile.open(tgz, "r:gz") as archive:
        names = archive.getnames()
        _assert_clean(names, "npm tgz")
        expected = {
            "package/LICENSE",
            "package/README.md",
            "package/THIRD_PARTY_NOTICES.md",
            "package/cordis.patch.yml",
            "package/index.js",
            "package/package.json",
            "package/skills/diepi-quant-research/SKILL.md",
            (
                "package/skills/diepi-quant-research/references/"
                "tushare-data-handoff.md"
            ),
        }
        files = {member.name for member in archive.getmembers() if member.isfile()}
        if files != expected:
            raise AssertionError(
                "unexpected npm payload:\n" + "\n".join(sorted(files ^ expected))
            )
        for member in archive.getmembers():
            if not member.isfile():
                continue
            if member.size > MAX_MEMBER_BYTES:
                raise AssertionError(f"npm tgz contains oversized release member: {member.name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise AssertionError(f"npm member could not be read: {member.name}")
            _assert_no_secrets(extracted.read(), "npm tgz", member.name)
        package_member = archive.extractfile("package/package.json")
        if package_member is None:
            raise AssertionError("npm tgz has no package.json")
        package = json.load(package_member)
        if package["version"] != "0.1.0-alpha.1":
            raise AssertionError("npm version is not 0.1.0-alpha.1")
        if package.get("publishConfig", {}).get("tag") != "alpha":
            raise AssertionError("npm publishConfig.tag must remain alpha")
        if package.get("repository", {}).get("directory") != "dsh":
            raise AssertionError("npm repository metadata must identify dsh/")
        if package.get("dsh", {}).get("bundle", {}).get("patch") != "./cordis.patch.yml":
            raise AssertionError("npm dsh.bundle.patch must identify ./cordis.patch.yml")
        handoff_member = archive.extractfile(
            "package/skills/diepi-quant-research/references/"
            "tushare-data-handoff.md"
        )
        if handoff_member is None:
            raise AssertionError("npm tgz has no Tushare handoff")
        handoff = handoff_member.read().decode("utf-8")
        handoff_requirements = (
            "5e12b31d09123e262c5fb38564e80c26d05cb830",
            "skills@1.5.23",
            "--skill tushare ",
            ".agents\\skills\\tushare\\SKILL.md",
            "No matching skills found",
        )
        for requirement in handoff_requirements:
            if requirement not in handoff:
                raise AssertionError(
                    f"Tushare handoff is missing release probe: {requirement}"
                )


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_artifacts.py DIST_DIR NPM_TGZ")
    dist = Path(sys.argv[1]).resolve(strict=True)
    tgz = Path(sys.argv[2]).resolve(strict=True)
    _check_python(dist)
    _check_npm(tgz)
    print("release-candidate payload checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
