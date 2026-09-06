"""Local artifact verification and cleanup helpers."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from nhk_easy_fetcher.audio import SubprocessRunner, validate_audio_output


def _ffprobe_path(ffmpeg_path: str) -> str:
    return str(Path(ffmpeg_path).with_name("ffprobe"))


@dataclass
class VerifyIssue:
    path: Path
    message: str


@dataclass
class VerifyReport:
    article_dir: Path
    ok: bool
    issues: list[VerifyIssue] = field(default_factory=list)


@dataclass
class CleanupAction:
    path: Path
    reason: str
    removed: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums_file(checksums_path: Path) -> dict[str, str]:
    """Return filename -> expected sha256 hex from checksums.sha256."""
    entries: dict[str, str] = {}
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        entries[name] = digest
    return entries


def _audio_paths(article_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in ("audio.m4a", "audio.mp3", "audio.m3u8"):
        path = article_dir / pattern
        if path.exists():
            candidates.append(path)
    return candidates


def verify_article_dir(
    article_dir: Path,
    *,
    check_audio: bool = False,
    ffmpeg_path: str = "ffmpeg",
) -> VerifyReport:
    issues: list[VerifyIssue] = []
    checksums_path = article_dir / "checksums.sha256"
    if not checksums_path.exists():
        issues.append(VerifyIssue(checksums_path, "missing checksums.sha256"))
        return VerifyReport(article_dir=article_dir, ok=False, issues=issues)

    try:
        expected = parse_checksums_file(checksums_path)
    except OSError as exc:
        issues.append(VerifyIssue(checksums_path, f"cannot read checksums: {exc}"))
        return VerifyReport(article_dir=article_dir, ok=False, issues=issues)

    if not expected:
        issues.append(VerifyIssue(checksums_path, "checksums.sha256 is empty"))

    for name, digest in expected.items():
        path = article_dir / name
        if not path.exists():
            issues.append(VerifyIssue(path, "file listed in checksums but missing"))
            continue
        actual = _sha256_file(path)
        if actual != digest:
            issues.append(
                VerifyIssue(path, f"hash mismatch (expected {digest[:12]}…, got {actual[:12]}…)")
            )

    if check_audio:
        audio_files = _audio_paths(article_dir)
        if not audio_files:
            issues.append(
                VerifyIssue(article_dir, "no audio artifact found for --audio verification")
            )
        else:
            runner = SubprocessRunner()
            ffprobe = _ffprobe_path(ffmpeg_path)
            for audio_path in audio_files:
                if audio_path.suffix == ".m3u8":
                    continue
                try:
                    validate_audio_output(
                        audio_path,
                        ffprobe_path=ffprobe,
                        runner=runner,
                    )
                except Exception as exc:  # noqa: BLE001 - surface as verify issue
                    issues.append(VerifyIssue(audio_path, str(exc)))

    return VerifyReport(article_dir=article_dir, ok=not issues, issues=issues)


def discover_article_dirs(output_dir: Path) -> list[Path]:
    articles_root = output_dir / "articles"
    if not articles_root.is_dir():
        return []
    return sorted(
        path
        for path in articles_root.rglob("*")
        if path.is_dir() and (path / "checksums.sha256").exists()
    )


def verify_output(
    output_dir: Path,
    *,
    article_dir: Path | None = None,
    check_audio: bool = False,
    ffmpeg_path: str = "ffmpeg",
) -> list[VerifyReport]:
    if article_dir is not None:
        return [
            verify_article_dir(
                article_dir.resolve(),
                check_audio=check_audio,
                ffmpeg_path=ffmpeg_path,
            )
        ]
    return [
        verify_article_dir(path, check_audio=check_audio, ffmpeg_path=ffmpeg_path)
        for path in discover_article_dirs(output_dir)
    ]


def _is_stale_partial(path: Path, *, older_than_days: int | None, now: float) -> bool:
    if older_than_days is None:
        return True
    age_seconds = now - path.stat().st_mtime
    return age_seconds >= older_than_days * 86400


def find_partial_files(output_dir: Path) -> list[Path]:
    meta_dir = output_dir / ".nhk-easy-fetcher"
    roots = [output_dir / "articles"]
    if meta_dir.is_dir():
        roots.append(meta_dir)
    partials: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        partials.extend(path for path in root.rglob("*.partial") if path.is_file())
    return sorted(partials)


def cleanup_partials(
    output_dir: Path,
    *,
    older_than_days: int | None = None,
    dry_run: bool = False,
) -> list[CleanupAction]:
    now = time.time()
    actions: list[CleanupAction] = []
    for path in find_partial_files(output_dir):
        if not _is_stale_partial(path, older_than_days=older_than_days, now=now):
            continue
        reason = "stale .partial temp file"
        if dry_run:
            actions.append(CleanupAction(path=path, reason=reason, removed=False))
            continue
        try:
            path.unlink()
            actions.append(CleanupAction(path=path, reason=reason, removed=True))
        except OSError as exc:
            actions.append(
                CleanupAction(path=path, reason=f"failed to remove: {exc}", removed=False)
            )
    return actions
