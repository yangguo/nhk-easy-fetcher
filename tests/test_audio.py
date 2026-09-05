import subprocess
from pathlib import Path

import pytest

from nhk_easy_fetcher.audio import (
    append_hdnts_token,
    build_ffmpeg_download_args,
    download_audio,
    resolve_hls_url,
    validate_manifest,
)
from nhk_easy_fetcher.errors import AudioUnavailable


def test_resolves_hls_from_voice_uri_not_article_id() -> None:
    hls = resolve_hls_url("voice-20260905.mp4")
    assert hls == "https://media.vd.st.nhk/news/easy_audio/voice-20260905/index.m3u8"


def test_resolves_hls_from_voice_uri_without_extension() -> None:
    hls = resolve_hls_url("voice-20260904de48127")
    assert hls == "https://media.vd.st.nhk/news/easy_audio/voice-20260904de48127/index.m3u8"


def test_refuses_html_error_page_as_manifest() -> None:
    with pytest.raises(AudioUnavailable):
        validate_manifest("<html>not found</html>", content_type="text/html")


def test_accepts_valid_m3u8(load_fixture) -> None:
    validate_manifest(load_fixture("master.m3u8"), content_type="application/vnd.apple.mpegurl")


def test_append_hdnts_token() -> None:
    url = "https://media.vd.st.nhk/news/easy_audio/voice-20260905/index.m3u8"
    signed = append_hdnts_token(url, "exp=123~acl=/*")
    assert "hdnts=exp%3D123~acl%3D%2F%2A" in signed


def test_build_ffmpeg_uses_reencode_not_copy() -> None:
    args = build_ffmpeg_download_args(
        ffmpeg_path="ffmpeg",
        manifest_url="https://media.vd.st.nhk/news/easy_audio/voice-20260905/index.m3u8",
        output_path=Path("/tmp/out.m4a"),
        mode="m4a",
        headers="Referer: https://news.web.nhk/news/easy/\r\n",
    )
    assert "-c:a" in args
    assert "aac" in args
    assert "-c copy" not in " ".join(args)


class FakeRunner:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.calls: list[list[str]] = []

    def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        self.output_path.write_bytes(b"fake audio")
        return subprocess.CompletedProcess(args, 0, "", "")


def test_download_audio_m4a(tmp_path: Path) -> None:
    partial = tmp_path / "audio.m4a.partial"
    runner = FakeRunner(partial)
    result = download_audio(
        news_easy_voice_uri="voice-20260905.mp4",
        output_dir=tmp_path,
        mode="m4a",
        runner=runner,
    )
    assert result.output_path == tmp_path / "audio.m4a"
    assert result.manifest_url.endswith("/voice-20260905/index.m3u8")
    assert "media.vd.st.nhk" in result.manifest_url
    assert "vod-stream.nhk.jp" not in result.manifest_url


def test_download_audio_mp3_uses_lame(tmp_path: Path) -> None:
    partial = tmp_path / "audio.mp3.partial"
    runner = FakeRunner(partial)
    download_audio(
        news_easy_voice_uri="voice-20260905.mp4",
        output_dir=tmp_path,
        mode="mp3",
        runner=runner,
    )
    assert "libmp3lame" in runner.calls[0]
