"""HLS audio resolution and ffmpeg download helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from nhk_easy_fetcher.errors import AudioUnavailable

HLS_AUDIO_BASE = "https://media.vd.st.nhk/news/easy_audio"
MANIFEST_MARKERS = ("#EXTM3U", "#EXT-X-")


class CommandRunner(Protocol):
    def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]: ...


@dataclass
class AudioDownloadResult:
    output_path: Path
    manifest_url: str
    format: str
    sha256: str | None = None


class SubprocessRunner:
    def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True, check=check)


def resolve_hls_url(news_easy_voice_uri: str) -> str:
    stem = Path(news_easy_voice_uri).stem
    if not stem or stem == ".":
        raise AudioUnavailable("missing voice URI")
    return f"{HLS_AUDIO_BASE}/{stem}/index.m3u8"


def validate_manifest(content: str, *, content_type: str | None = None) -> None:
    if content_type and "html" in content_type.lower():
        raise AudioUnavailable("manifest response is HTML, not HLS")
    if not any(marker in content for marker in MANIFEST_MARKERS):
        raise AudioUnavailable("response is not a valid HLS manifest")


def append_hdnts_token(manifest_url: str, hdnts: str | None) -> str:
    """Append Akamai hdnts query token when provided by the session."""
    if not hdnts:
        return manifest_url
    parsed = urlparse(manifest_url)
    query = parse_qs(parsed.query)
    query["hdnts"] = [hdnts]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def extract_hdnts_from_cookies(cookies: dict[str, str]) -> str | None:
    """Return hdnts token if explicitly stored in the cookie jar metadata."""
    for key in ("hdnts", "HDNTS", "akamai_hdnts"):
        value = cookies.get(key)
        if value:
            return value
    # z_at is required to mint hdnts in the NHK player, but minting is not implemented here.
    if cookies.get("z_at"):
        return None
    return None


def build_ffmpeg_headers(
    *,
    cookies: dict[str, str],
    referer: str = "https://news.web.nhk/news/easy/",
) -> str:
    cookie_header = "; ".join(f"{name}={value}" for name, value in cookies.items())
    lines = [f"Referer: {referer}"]
    if cookie_header:
        lines.append(f"Cookie: {cookie_header}")
    return "\r\n".join(lines) + "\r\n"


def ffmpeg_audio_codec(mode: str) -> tuple[str, str]:
    if mode == "mp3":
        return "libmp3lame", "mp3"
    return "aac", "m4a"


def build_ffmpeg_download_args(
    *,
    ffmpeg_path: str,
    manifest_url: str,
    output_path: Path,
    mode: str,
    headers: str,
) -> list[str]:
    codec, _ = ffmpeg_audio_codec(mode)
    return [
        ffmpeg_path,
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-headers",
        headers,
        "-i",
        manifest_url,
        "-c:a",
        codec,
        "-b:a",
        "128k",
        str(output_path),
    ]


def download_audio(
    *,
    news_easy_voice_uri: str,
    output_dir: Path,
    mode: str,
    ffmpeg_path: str = "ffmpeg",
    cookies: dict[str, str] | None = None,
    hdnts: str | None = None,
    runner: CommandRunner | None = None,
    keep_manifest: bool = False,
) -> AudioDownloadResult:
    if mode not in {"m4a", "mp3", "manifest"}:
        raise AudioUnavailable(f"unsupported audio mode: {mode}")

    manifest_url = resolve_hls_url(news_easy_voice_uri)
    cookie_map = cookies or {}
    token = hdnts or extract_hdnts_from_cookies(cookie_map)
    manifest_url = append_hdnts_token(manifest_url, token)

    _, ext = ffmpeg_audio_codec(mode if mode != "manifest" else "m4a")
    if mode == "manifest":
        ext = "m3u8"

    output_path = output_dir / f"audio.{ext}.partial"
    output_final = output_dir / f"audio.{ext}"

    if mode == "manifest":
        # Caller should fetch manifest separately; mark path for manifest-only mode.
        return AudioDownloadResult(
            output_path=output_final,
            manifest_url=manifest_url,
            format="manifest",
        )

    headers = build_ffmpeg_headers(cookies=cookie_map)
    args = build_ffmpeg_download_args(
        ffmpeg_path=ffmpeg_path,
        manifest_url=manifest_url,
        output_path=output_path,
        mode=mode,
        headers=headers,
    )

    proc_runner = runner or SubprocessRunner()
    try:
        completed = proc_runner.run(args)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        if "403" in stderr or "401" in stderr:
            raise AudioUnavailable(
                "HLS download rejected (403/401). Akamai hdnts token is required; "
                "see README — token minting from z_at cookie is not yet implemented."
            ) from exc
        raise AudioUnavailable(f"ffmpeg failed: {stderr.strip() or exc}") from exc

    if completed.returncode != 0:
        raise AudioUnavailable(f"ffmpeg failed: {completed.stderr.strip()}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioUnavailable("ffmpeg produced an empty audio file")

    output_path.replace(output_final)
    return AudioDownloadResult(
        output_path=output_final,
        manifest_url=manifest_url,
        format=ext,
    )


def save_manifest_placeholder(
    output_dir: Path,
    manifest_url: str,
    manifest_body: str,
) -> Path:
    validate_manifest(manifest_body)
    path = output_dir / "audio.m3u8"
    path.write_text(manifest_body, encoding="utf-8")
    return path
