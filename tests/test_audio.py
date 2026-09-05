import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from nhk_easy_fetcher.audio import (
    append_hdnts_token,
    authorize_manifest_url,
    build_ffmpeg_download_args,
    download_audio,
    is_remote_manifest,
    mint_hdnts_token,
    prepare_ffmpeg_manifest_input,
    resolve_hls_url,
    rewrite_local_manifest_with_hdnts,
    validate_audio_output,
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


@respx.mock
def test_z_at_cookies_mint_hdnts_via_mediatoken() -> None:
    manifest = "https://media.vd.st.nhk/news/easy_audio/voice-20260904de48127/index.m3u8"
    route = respx.post("https://mediatoken.web.nhk/v1/token").respond(
        200,
        json={"token": "exp=1700000000~acl=/news/easy_audio/*"},
    )
    authorized = authorize_manifest_url(manifest, {"z_at": "jwt-example"})
    assert route.called
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer jwt-example"
    assert json_body(request) == {"url": manifest}
    assert "hdnts=exp" in authorized


def test_authorize_manifest_url_uses_precomputed_hdnts_without_mediatoken() -> None:
    manifest = "https://media.vd.st.nhk/news/easy_audio/voice-20260905/index.m3u8"
    authorized = authorize_manifest_url(manifest, {"hdnts": "exp=123~acl=/*"})
    assert "hdnts=exp%3D123" in authorized


def test_build_ffmpeg_remote_uses_https_input_without_protocol_whitelist() -> None:
    url = "https://media.vd.st.nhk/news/easy_audio/voice-20260905/index.m3u8?hdnts=exp"
    args = build_ffmpeg_download_args(
        ffmpeg_path="ffmpeg",
        manifest_input=url,
        output_path=Path("/tmp/out.m4a"),
        mode="m4a",
        headers="Referer: https://news.web.nhk/news/easy/\r\n",
        is_remote=True,
    )
    assert "-protocol_whitelist" not in args
    assert args[args.index("-i") + 1] == url
    assert "aac" in args
    assert "64k" in args
    assert "-c copy" not in " ".join(args)


def test_build_ffmpeg_local_uses_protocol_whitelist() -> None:
    local = "/tmp/audio.input.m3u8"
    args = build_ffmpeg_download_args(
        ffmpeg_path="ffmpeg",
        manifest_input=local,
        output_path=Path("/tmp/out.m4a"),
        mode="m4a",
        headers="Referer: https://news.web.nhk/news/easy/\r\n",
        is_remote=False,
    )
    assert "-protocol_whitelist" in args
    assert "file,http,https,tcp,tls,crypto" in args
    assert args[args.index("-i") + 1] == local


def test_build_ffmpeg_mp3_uses_lame() -> None:
    args = build_ffmpeg_download_args(
        ffmpeg_path="ffmpeg",
        manifest_input="https://example.test/index.m3u8",
        output_path=Path("/tmp/out.mp3"),
        mode="mp3",
        headers="",
        is_remote=True,
    )
    assert "libmp3lame" in args


def test_rewrite_local_manifest_with_hdnts() -> None:
    body = "#EXTM3U\n#EXTINF:10,\nsegment0.aac\n"
    rewritten = rewrite_local_manifest_with_hdnts(body, "exp=123")
    assert "segment0.aac?hdnts=exp=123" in rewritten


def test_prepare_ffmpeg_manifest_input_remote() -> None:
    manifest = "https://media.vd.st.nhk/news/easy_audio/voice-20260905/index.m3u8"
    ffmpeg_input, is_remote = prepare_ffmpeg_manifest_input(
        manifest,
        {"hdnts": "exp=123"},
        output_dir=Path("/tmp"),
    )
    assert is_remote is True
    assert ffmpeg_input.startswith("https://")
    assert "hdnts=" in ffmpeg_input


def test_prepare_ffmpeg_manifest_input_local(tmp_path: Path) -> None:
    manifest_path = tmp_path / "local.m3u8"
    manifest_path.write_text("#EXTM3U\nsegment0.aac\n", encoding="utf-8")
    ffmpeg_input, is_remote = prepare_ffmpeg_manifest_input(
        str(manifest_path),
        {"hdnts": "exp=local"},
        output_dir=tmp_path,
    )
    assert is_remote is False
    assert Path(ffmpeg_input).exists()
    assert "hdnts=exp=local" in Path(ffmpeg_input).read_text(encoding="utf-8")


class FakeRunner:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.calls: list[list[str]] = []

    def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if Path(args[0]).name == "ffprobe":
            return subprocess.CompletedProcess(
                args,
                0,
                '{"streams":[{"codec_type":"audio"}],"format":{"duration":"1"}}',
                "",
            )
        self.output_path.write_bytes(b"fake audio")
        return subprocess.CompletedProcess(args, 0, "", "")


@respx.mock
def test_download_audio_m4a_uses_remote_https_input(tmp_path: Path) -> None:
    token = respx.post("https://mediatoken.web.nhk/v1/token").respond(
        200,
        json={"token": "exp=1700000000~acl=/news/easy_audio/*"},
    )
    partial = tmp_path / "audio.m4a.partial"
    runner = FakeRunner(partial)
    result = download_audio(
        news_easy_voice_uri="voice-20260904de48127.mp4",
        output_dir=tmp_path,
        mode="m4a",
        cookies={"z_at": "jwt-example"},
        runner=runner,
    )
    assert result.output_path == tmp_path / "audio.m4a"
    assert "hdnts=" in result.manifest_url
    assert "media.vd.st.nhk" in result.manifest_url
    assert "-protocol_whitelist" not in runner.calls[0]
    ffmpeg_input = runner.calls[0][runner.calls[0].index("-i") + 1]
    assert ffmpeg_input.startswith("https://")
    assert "hdnts=" in ffmpeg_input
    assert token.call_count == 1


def test_download_audio_mp3_uses_lame(tmp_path: Path) -> None:
    partial = tmp_path / "audio.mp3.partial"
    runner = FakeRunner(partial)
    download_audio(
        news_easy_voice_uri="voice-20260905.mp4",
        output_dir=tmp_path,
        mode="mp3",
        cookies={"hdnts": "exp=123"},
        runner=runner,
    )
    assert "libmp3lame" in runner.calls[0]


class InvalidAudioRunner(FakeRunner):
    def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if Path(args[0]).name == "ffprobe":
            return subprocess.CompletedProcess(
                args,
                0,
                '{"streams":[],"format":{"duration":"1"}}',
                "",
            )
        self.output_path.write_bytes(b"not an audio container")
        return subprocess.CompletedProcess(args, 0, "", "")


def test_download_audio_rejects_output_without_audio_stream(tmp_path: Path) -> None:
    runner = InvalidAudioRunner(tmp_path / "audio.m4a.partial")

    with pytest.raises(AudioUnavailable, match="audio stream"):
        download_audio(
            news_easy_voice_uri="voice-20260905.mp4",
            output_dir=tmp_path,
            mode="m4a",
            cookies={"hdnts": "exp=123"},
            runner=runner,
        )


def test_validate_audio_output_rejects_nonobject_probe_json(tmp_path: Path) -> None:
    class ProbeRunner:
        def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 0, "[]", "")

    with pytest.raises(AudioUnavailable, match="invalid JSON"):
        validate_audio_output(
            tmp_path / "audio.m4a.partial",
            ffprobe_path="ffprobe",
            runner=ProbeRunner(),
        )


def test_mint_hdnts_token_raises_on_error_payload() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "error": {"message": "invalid token"},
    }

    def fake_post(*_args: object, **_kwargs: object) -> httpx.Response:
        return response

    with pytest.raises(AudioUnavailable, match="mediatoken error"):
        mint_hdnts_token("https://example.test/index.m3u8", "bad", post_json=fake_post)


def json_body(request: httpx.Request) -> dict[str, object]:
    import json

    return json.loads(request.content.decode("utf-8"))


def test_is_remote_manifest() -> None:
    assert is_remote_manifest("https://media.vd.st.nhk/x.m3u8") is True
    assert is_remote_manifest("/tmp/x.m3u8") is False
