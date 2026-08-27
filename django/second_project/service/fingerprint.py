"""File fingerprinting used by Bronze manifests and integrity checks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileFingerprint:
    """Immutable file metadata captured for one loading attempt."""

    path: Path
    size_bytes: int
    sha256: str


class FileFingerprintError(RuntimeError):
    """Raised when a source file cannot be fingerprinted safely."""


def fingerprint_file(path: Path, *, chunk_size: int = 1024 * 1024) -> FileFingerprint:
    """Read a file in chunks and calculate its SHA-256 digest."""

    if not path.is_file():
        raise FileFingerprintError(f"입력 파일이 없습니다: {path.name}")

    try:
        before_size = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        after_size = path.stat().st_size
    except OSError as exc:
        raise FileFingerprintError("입력 파일의 체크섬을 계산하지 못했습니다.") from exc

    if before_size != after_size:
        raise FileFingerprintError(
            "적재 중 입력 파일 크기가 변경되었습니다. 원본 파일을 고정한 뒤 다시 실행하세요."
        )

    return FileFingerprint(path=path, size_bytes=after_size, sha256=digest.hexdigest())


def relative_path(path: Path, root: Path) -> str:
    """Return a non-sensitive project-relative path for a manifest."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


