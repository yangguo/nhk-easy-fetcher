from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def make_lrc() -> ModuleType:
    script = Path(__file__).parents[1] / "scripts" / "make_lrc.py"
    spec = importlib.util.spec_from_file_location("make_lrc", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_sentences_keeps_terminator_runs_together(make_lrc: ModuleType) -> None:
    assert make_lrc.split_sentences("「本当！？」次です。！？") == ["「本当！？」", "次です。！？"]
    assert make_lrc.split_sentences("！？   。") == []


def test_timestamp_formatters_clamp_and_round(make_lrc: ModuleType) -> None:
    assert make_lrc.fmt_lrc_time(-1) == "[00:00.00]"
    assert make_lrc.fmt_lrc_time(61.239) == "[01:01.24]"
    assert make_lrc.fmt_srt_time(-1) == "00:00:00,000"
    assert make_lrc.fmt_srt_time(3661.2346) == "01:01:01,235"


def test_write_lrc_includes_metadata_and_entries(tmp_path: Path, make_lrc: ModuleType) -> None:
    output = tmp_path / "audio.lrc"
    result = make_lrc.write_lrc(output, "題名", "article-1", [(1.25, "一行目")], duration=9.5)

    assert result == output
    assert output.read_text(encoding="utf-8").splitlines() == [
        "[ti:題名]",
        "[ar:NHK NEWS WEB EASY]",
        "[al:article-1]",
        "[by:nhk-easy-fetcher scripts/make_lrc.py + faster-whisper]",
        "[comment:Personal study only - do not redistribute]",
        "[length:00:09.50]",
        "[00:01.25]一行目",
    ]


def test_write_srt_does_not_overlap_short_cues_and_clamps_last(
    tmp_path: Path, make_lrc: ModuleType
) -> None:
    output = tmp_path / "audio.srt"
    make_lrc.write_srt(
        output,
        [(0.0, "one"), (0.25, "two"), (2.75, "three")],
        duration=3.0,
    )

    assert output.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:00,250\none\n\n"
        "2\n00:00:00,250 --> 00:00:02,750\ntwo\n\n"
        "3\n00:00:02,750 --> 00:00:03,000\nthree\n"
    )


def test_write_srt_never_writes_end_before_start(tmp_path: Path, make_lrc: ModuleType) -> None:
    output = tmp_path / "audio.srt"
    make_lrc.write_srt(output, [(5.0, "late")], duration=4.0)

    assert "00:00:04,000 --> 00:00:04,000" in output.read_text(encoding="utf-8")


def test_map_time_handles_exact_interpolated_and_edge_indices(make_lrc: ModuleType) -> None:
    mapping = {2: 0, 6: 2}
    article_indices = [2, 6]
    character_times = [1.0, 2.0, 3.0]

    assert make_lrc.map_time(2, mapping, article_indices, character_times) == 1.0
    assert make_lrc.map_time(4, mapping, article_indices, character_times) == 2.0
    assert make_lrc.map_time(0, mapping, article_indices, character_times) == 1.0
    assert make_lrc.map_time(8, mapping, article_indices, character_times) == 3.0
    assert make_lrc.map_time(0, {}, [], []) is None


def test_interpolate_times_spreads_runs_and_clamps_to_duration(make_lrc: ModuleType) -> None:
    assert make_lrc.interpolate_times([1.0, None, None, 7.0], 8.0) == [1.0, 3.0, 5.0, 7.0]
    assert make_lrc.interpolate_times([None, 12.0, None], 10.0) == [5.0, 10.0, 10.0]


def test_monotonic_entries_adjusts_or_drops_duplicate_timestamps(make_lrc: ModuleType) -> None:
    units = [("body", "one"), ("body", "two")]
    assert make_lrc.monotonic_entries(units, [1.0, 1.0], 2.0) == [
        (1.0, "one"),
        (1.01, "two"),
    ]
    assert make_lrc.monotonic_entries(units, [1.0, 1.0], 1.0) == [(1.0, "one")]


def test_expand_path_expands_home_and_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_lrc: ModuleType
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert make_lrc.expand_path("~/articles/../saved") == (tmp_path / "saved").resolve()


def _write_article(directory: Path, audio_format: str | None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    article_json = directory / "article.json"
    article_json.write_text(json.dumps({"audio": {"format": audio_format}}), encoding="utf-8")
    return article_json


def test_audio_discovery_supports_m4a_and_mp3_and_prefers_metadata(
    tmp_path: Path, make_lrc: ModuleType
) -> None:
    m4a_dir = tmp_path / "m4a"
    m4a_json = _write_article(m4a_dir, "m4a")
    (m4a_dir / "audio.m4a").touch()

    mp3_dir = tmp_path / "mp3"
    mp3_json = _write_article(mp3_dir, "mp3")
    (mp3_dir / "audio.mp3").touch()

    both_dir = tmp_path / "both"
    both_json = _write_article(both_dir, "mp3")
    (both_dir / "audio.m4a").touch()
    (both_dir / "audio.mp3").touch()

    assert make_lrc.discover_targets(tmp_path) == sorted([m4a_json, mp3_json, both_json])
    data = json.loads(both_json.read_text(encoding="utf-8"))
    assert make_lrc.find_audio_path(both_dir, data) == both_dir / "audio.mp3"


def test_main_rejects_single_article_without_audio_before_model_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_lrc: ModuleType
) -> None:
    _write_article(tmp_path, "m4a")

    assert make_lrc.main([str(tmp_path)]) == 2
    assert "no articles with audio" in capsys.readouterr().err


def test_align_uses_mp3_and_whisper_duration_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_lrc: ModuleType
) -> None:
    article_json = _write_article(tmp_path, "mp3")
    article_json.write_text(
        json.dumps(
            {
                "article_id": "article-1",
                "title": {"plain": ""},
                "paragraphs": [{"plain": "猫です。"}],
                "audio": {"format": "mp3"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audio_path = tmp_path / "audio.mp3"
    audio_path.touch()
    word = SimpleNamespace(word="猫です", start=0.25, end=1.0)
    segment = SimpleNamespace(words=[word])

    class FakeModel:
        transcribed_path: str | None = None

        def transcribe(self, path: str, **_kwargs: object) -> tuple[list[object], object]:
            self.transcribed_path = path
            return [segment], SimpleNamespace(duration=1.5)

    model = FakeModel()
    monkeypatch.setattr(make_lrc, "audio_duration", lambda _path: None)

    assert make_lrc.align(tmp_path, model, "fake", write_srt_file=False) == tmp_path / "audio.lrc"
    assert model.transcribed_path == str(audio_path)
    assert "[length:00:01.50]" in (tmp_path / "audio.lrc").read_text(encoding="utf-8")


def test_build_mkv_mux_args_copies_audio_and_embeds_srt(
    tmp_path: Path, make_lrc: ModuleType
) -> None:
    args = make_lrc.build_mkv_mux_args(
        "ffmpeg", tmp_path / "audio.m4a", tmp_path / "audio.srt", tmp_path / "audio.mkv"
    )

    assert args[0] == "ffmpeg"
    assert args[-1] == str(tmp_path / "audio.mkv")
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "copy"
    assert "-c:s" in args and args[args.index("-c:s") + 1] == "srt"
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-disposition:s:0") + 1] == "default"
    assert args[args.index("-metadata:s:s:0") + 1] == "language=jpn"
    maps = [args[i + 1] for i, flag in enumerate(args) if flag == "-map"]
    assert maps == ["1:a", "0:v", "2:s"]


def test_resolve_ffmpeg_prefers_env_path(
    tmp_path: Path, make_lrc: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    fake.write_text("", encoding="utf-8")

    monkeypatch.setenv("NHK_EASY_FFMPEG_PATH", str(fake))
    assert make_lrc.resolve_ffmpeg() == str(fake)

    monkeypatch.setenv("NHK_EASY_FFMPEG_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(make_lrc.shutil, "which", lambda _name: None)
    assert make_lrc.resolve_ffmpeg() is None


def test_mux_mkv_skips_without_srt(tmp_path: Path, make_lrc: ModuleType) -> None:
    audio_path = tmp_path / "audio.m4a"
    audio_path.touch()
    assert make_lrc.mux_mkv(tmp_path, audio_path) is None


def test_mux_mkv_runs_ffmpeg_and_returns_output(
    tmp_path: Path, make_lrc: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_path = tmp_path / "audio.m4a"
    audio_path.touch()
    (tmp_path / "audio.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    monkeypatch.setenv("NHK_EASY_FFMPEG_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(make_lrc.shutil, "which", lambda _name: "ffmpeg")

    def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        Path(args[-1]).touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(make_lrc.subprocess, "run", fake_run)
    assert make_lrc.mux_mkv(tmp_path, audio_path) == tmp_path / "audio.mkv"


def test_mux_mkv_reports_failure_without_raising(
    tmp_path: Path,
    make_lrc: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audio_path = tmp_path / "audio.m4a"
    audio_path.touch()
    (tmp_path / "audio.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    monkeypatch.setenv("NHK_EASY_FFMPEG_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(make_lrc.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        make_lrc.subprocess,
        "run",
        lambda _args, **_kwargs: SimpleNamespace(returncode=1, stderr="boom\nlibx264 error"),
    )

    assert make_lrc.mux_mkv(tmp_path, audio_path) is None
    assert "audio.mkv mux failed: libx264 error" in capsys.readouterr().out
