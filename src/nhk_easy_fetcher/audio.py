"""HLS audio resolution and ffmpeg download helpers."""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from nhk_easy_fetcher.client import DEFAULT_USER_AGENT
from nhk_easy_fetcher.errors import AudioUnavailable

HLS_AUDIO_BASE = "https://media.vd.st.nhk/news/easy_audio"
MEDIATOKEN_URL = "https://mediatoken.web.nhk/v1/token"
MANIFEST_MARKERS = ("#EXTM3U", "#EXT-X-")
PROTOCOL_WHITELIST = "file,http,https,tcp,tls,crypto"


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
    return None


def _parse_mediatoken_response(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message", "mediatoken request failed")
        raise AudioUnavailable(f"mediatoken error: {message}")

    for key in ("hdnts", "token"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value

    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("hdnts", "token"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value

    raise AudioUnavailable("mediatoken response missing hdnts/token")


def mint_hdnts_token(
    manifest_url: str,
    z_at: str,
    *,
    post_json: Callable[..., httpx.Response] | None = None,
) -> str:
    """Mint an Akamai hdnts token for a manifest URL using the NHK mediatoken service."""
    poster = post_json or httpx.post
    response = poster(
        MEDIATOKEN_URL,
        json={"url": manifest_url},
        headers={
            "Authorization": f"Bearer {z_at}",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        timeout=20,
    )
    if response.status_code in {401, 403}:
        raise AudioUnavailable("mediatoken rejected authorization (check z_at cookie)")
    if response.status_code >= 400:
        raise AudioUnavailable(f"mediatoken HTTP {response.status_code}")
    return _parse_mediatoken_response(response.json())


def authorize_manifest_url(
    manifest_url: str,
    cookies: dict[str, str],
    *,
    post_json: Callable[..., httpx.Response] | None = None,
) -> str:
    """Return a manifest URL with hdnts appended, minting via mediatoken when needed."""
    parsed = urlparse(manifest_url)
    if parse_qs(parsed.query).get("hdnts"):
        return manifest_url
    hdnts = extract_hdnts_from_cookies(cookies)
    if not hdnts:
        z_at = cookies.get("z_at")
        if z_at:
            hdnts = mint_hdnts_token(manifest_url, z_at, post_json=post_json)
    return append_hdnts_token(manifest_url, hdnts)


def is_remote_manifest(manifest_input: str) -> bool:
    return manifest_input.startswith("http://") or manifest_input.startswith("https://")


def rewrite_local_manifest_with_hdnts(manifest_body: str, hdnts: str) -> str:
    """Rewrite segment lines in a local playlist to include the hdnts query token."""
    rewritten: list[str] = []
    for line in manifest_body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            separator = "&" if "?" in stripped else "?"
            line = f"{stripped}{separator}hdnts={hdnts}"
        rewritten.append(line)
    return "\n".join(rewritten) + "\n"


def prepare_ffmpeg_manifest_input(
    manifest_url: str,
    cookies: dict[str, str],
    *,
    output_dir: Path,
    post_json: Callable[..., httpx.Response] | None = None,
) -> tuple[str, bool]:
    """Return ffmpeg manifest input and whether it is a remote URL."""
    if is_remote_manifest(manifest_url):
        authorized = authorize_manifest_url(manifest_url, cookies, post_json=post_json)
        return authorized, True

    manifest_path = Path(manifest_url)
    if not manifest_path.exists():
        raise AudioUnavailable(f"local manifest not found: {manifest_path}")

    hdnts = extract_hdnts_from_cookies(cookies)
    if not hdnts and cookies.get("z_at"):
        raise AudioUnavailable(
            "local manifest requires a precomputed hdnts cookie; mint tokens against the remote URL"
        )

    body = manifest_path.read_text(encoding="utf-8")
    if hdnts:
        body = rewrite_local_manifest_with_hdnts(body, hdnts)
    temp_manifest = output_dir / "audio.input.m3u8.partial"
    temp_manifest.write_text(body, encoding="utf-8")
    return str(temp_manifest), False


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


def _ffprobe_path(ffmpeg_path: str) -> str:
    return str(Path(ffmpeg_path).with_name("ffprobe"))


def validate_audio_output(
    output_path: Path,
    *,
    ffprobe_path: str,
    runner: CommandRunner,
) -> None:
    """Require a readable container with an audio stream and positive duration."""
    args = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type:format=duration",
        "-of",
        "json",
        str(output_path),
    ]
    try:
        result = runner.run(args)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AudioUnavailable(f"ffprobe failed: {exc}") from exc
    if result.returncode != 0:
        raise AudioUnavailable(f"ffprobe failed: {result.stderr.strip() or 'unknown error'}")
    try:
        payload = json.loads(result.stdout or "")
    except (TypeError, ValueError) as exc:
        raise AudioUnavailable("ffprobe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise AudioUnavailable("ffprobe returned invalid JSON")

    streams = payload.get("streams")
    if not isinstance(streams, list) or not any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
    ):
        raise AudioUnavailable("ffprobe found no audio stream")
    format_info = payload.get("format")
    duration_value = format_info.get("duration") if isinstance(format_info, dict) else None
    if not isinstance(duration_value, (str, int, float)):
        raise AudioUnavailable("ffprobe returned an invalid audio duration")
    try:
        duration = float(duration_value)
    except (TypeError, ValueError) as exc:
        raise AudioUnavailable("ffprobe returned an invalid audio duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise AudioUnavailable("ffprobe found a non-positive audio duration")


def build_ffmpeg_download_args(
    *,
    ffmpeg_path: str,
    manifest_input: str,
    output_path: Path,
    mode: str,
    headers: str,
    is_remote: bool,
) -> list[str]:
    codec, _ = ffmpeg_audio_codec(mode)
    args = [
        ffmpeg_path,
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
    ]
    if not is_remote:
        args.extend(["-protocol_whitelist", PROTOCOL_WHITELIST])
    args.extend(
        [
            "-headers",
            headers,
            "-i",
            manifest_input,
            "-c:a",
            codec,
        ]
    )
    if mode == "m4a":
        args.extend(["-b:a", "64k"])
    elif mode == "mp3":
        args.extend(["-b:a", "128k"])
    args.append(str(output_path))
    return args


def download_audio(
    *,
    news_easy_voice_uri: str,
    output_dir: Path,
    mode: str,
    ffmpeg_path: str = "ffmpeg",
    cookies: dict[str, str] | None = None,
    manifest_url: str | None = None,
    runner: CommandRunner | None = None,
    post_json: Callable[..., httpx.Response] | None = None,
) -> AudioDownloadResult:
    if mode not in {"m4a", "mp3", "manifest"}:
        raise AudioUnavailable(f"unsupported audio mode: {mode}")

    cookie_map = cookies or {}
    base_manifest = manifest_url or resolve_hls_url(news_easy_voice_uri)
    authorized_manifest = authorize_manifest_url(
        base_manifest,
        cookie_map,
        post_json=post_json,
    )

    _, ext = ffmpeg_audio_codec(mode if mode != "manifest" else "m4a")
    if mode == "manifest":
        ext = "m3u8"

    output_path = output_dir / f"audio.{ext}.partial"
    output_final = output_dir / f"audio.{ext}"

    if mode == "manifest":
        return AudioDownloadResult(
            output_path=output_final,
            manifest_url=authorized_manifest,
            format="manifest",
        )

    if is_remote_manifest(base_manifest):
        # We already authorized the remote URL above.  Passing it directly
        # avoids a second mediatoken request and preserves the signed URL.
        manifest_input, is_remote = authorized_manifest, True
    else:
        manifest_input, is_remote = prepare_ffmpeg_manifest_input(
            base_manifest,
            cookie_map,
            output_dir=output_dir,
            post_json=post_json,
        )

    headers = build_ffmpeg_headers(cookies=cookie_map)
    args = build_ffmpeg_download_args(
        ffmpeg_path=ffmpeg_path,
        manifest_input=manifest_input,
        output_path=output_path,
        mode=mode,
        headers=headers,
        is_remote=is_remote,
    )

    proc_runner = runner or SubprocessRunner()
    try:
        completed = proc_runner.run(args)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        if "403" in stderr or "401" in stderr:
            raise AudioUnavailable(
                "HLS download rejected (403/401). Ensure z_at cookie is valid and "
                "mediatoken can mint hdnts for the manifest URL."
            ) from exc
        raise AudioUnavailable(f"ffmpeg failed: {stderr.strip() or exc}") from exc

    if completed.returncode != 0:
        raise AudioUnavailable(f"ffmpeg failed: {completed.stderr.strip()}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioUnavailable("ffmpeg produced an empty audio file")

    validate_audio_output(
        output_path,
        ffprobe_path=_ffprobe_path(ffmpeg_path),
        runner=proc_runner,
    )

    output_path.replace(output_final)
    return AudioDownloadResult(
        output_path=output_final,
        manifest_url=authorized_manifest,
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
