"""Verify the downloaded release-candidate payload against SHA256SUMS."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import sys


LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_sha256s.py SHA256SUMS CANDIDATE_ROOT")
    sums = Path(sys.argv[1]).resolve(strict=True)
    root = Path(sys.argv[2]).resolve(strict=True)
    expected: dict[str, str] = {}
    for number, line in enumerate(sums.read_text(encoding="utf-8").splitlines(), 1):
        match = LINE.fullmatch(line)
        if match is None:
            raise AssertionError(f"invalid SHA256SUMS line {number}")
        digest, relative_text = match.groups()
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise AssertionError(f"unsafe SHA256SUMS path on line {number}")
        normalized = relative.as_posix()
        if normalized in expected:
            raise AssertionError(f"duplicate SHA256SUMS path: {normalized}")
        expected[normalized] = digest

    actual = {
        path.relative_to(root).as_posix()
        for folder in (root / "dist", root / "packed")
        for path in folder.iterdir()
        if path.is_file()
    }
    if set(expected) != actual:
        raise AssertionError(
            "SHA256SUMS payload mismatch: "
            f"missing={sorted(actual - set(expected))}, "
            f"unexpected={sorted(set(expected) - actual)}"
        )
    for relative, wanted in expected.items():
        target = (root / Path(*PurePosixPath(relative).parts)).resolve(strict=True)
        target.relative_to(root)
        observed = _digest(target)
        if observed != wanted:
            raise AssertionError(f"SHA-256 mismatch: {relative}")
    print("release-candidate SHA-256 verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
