"""Generate synced-lyrics files for fetched NHK EASY articles.

Aligns each article's ``article.json`` sentences against its ``audio.m4a`` or
``audio.mp3`` with faster-whisper word timestamps, then writes ``audio.lrc`` next to the
audio file (same basename, so lyric-capable players auto-load it) plus
``audio.srt`` for players without LRC support (``--no-srt`` to skip).

VLC (desktop 3.x) loads same-basename SRT for audio files but then drops
every subtitle frame because audio-only media has no video output window
("no vout found, dropping subpicture"). For VLC, an ``audio.mkv`` is also
muxed: the audio stream copied with the SRT embedded and a minimal
still-video track so the subtitle renderer has something to draw on
(``--no-mkv`` to skip; needs ``ffmpeg`` on PATH or ``NHK_EASY_FFMPEG_PATH``).

Usage:
    pip install -e '.[lrc]'
    python scripts/make_lrc.py ~/NHK-Easy/articles
    python scripts/make_lrc.py ~/NHK-Easy/articles/2026/2026-09/2026-09-07_20260907de48812

On some Windows machines the model fails to load with
``RuntimeError: mkl_malloc: failed to allocate memory``; limiting the
thread pools works around it:
    set MKL_NUM_THREADS=2
    set OMP_NUM_THREADS=2

NHK content is for personal study only — do not redistribute
generated lyrics files.
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

_SENT_RE = re.compile(r"[^。！？]*[。！？]+[」』）\"”’]*|[^。！？]+$")
_STRIP_RE = re.compile(
    r"[\s、。，．・「」『』（）()［\]<>〈〉《》…‥—―～~"
    r"!！?？:：;；,，.．\"“”'‘’/／＼\\|｜+＋*＊#＃%％&＆@＠·]+"
)


def normalize(text: str) -> str:
    """NFKC-normalize and drop whitespace/punctuation for fuzzy matching."""
    return _STRIP_RE.sub("", unicodedata.normalize("NFKC", text))


def split_sentences(text: str) -> list[str]:
    """Split Japanese text into sentences, keeping closing brackets."""
    return [
        sentence
        for match in _SENT_RE.finditer(text.strip())
        if (sentence := match.group(0).strip()) and normalize(sentence)
    ]


def expand_path(value: str) -> Path:
    """Expand a user path and make it absolute without requiring it to exist."""
    return Path(value).expanduser().resolve()


def find_audio_path(article_dir: Path, data: dict[str, object] | None = None) -> Path | None:
    """Find supported article audio, preferring article.json's declared format."""
    formats = ["m4a", "mp3"]
    if data is not None:
        audio = data.get("audio")
        preferred = audio.get("format") if isinstance(audio, dict) else None
        if isinstance(preferred, str) and preferred.lower() in formats:
            preferred = preferred.lower()
            formats = [preferred, *(item for item in formats if item != preferred)]
    for audio_format in formats:
        candidate = article_dir / f"audio.{audio_format}"
        if candidate.is_file():
            return candidate
    return None


def discover_targets(root: Path) -> list[Path]:
    """Return article.json files that have a supported local audio file."""
    candidates = (
        [root / "article.json"] if (root / "article.json").is_file() else root.rglob("article.json")
    )
    targets: list[Path] = []
    for article_json in candidates:
        try:
            data = json.loads(article_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if find_audio_path(article_json.parent, data if isinstance(data, dict) else None):
            targets.append(article_json)
    return sorted(targets)


def fmt_lrc_time(seconds: float) -> str:
    total_cs = int(round(max(0.0, seconds) * 100))
    minutes, rem = divmod(total_cs, 6000)
    return f"[{minutes:02d}:{rem / 100:05.2f}]"


def fmt_srt_time(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(total_ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_lrc(
    path: Path,
    title: str,
    article_id: str,
    entries: list[tuple[float, str]],
    duration: float | None,
) -> Path:
    lines = [
        f"[ti:{title}]",
        "[ar:NHK NEWS WEB EASY]",
        f"[al:{article_id}]",
        "[by:nhk-easy-fetcher scripts/make_lrc.py + faster-whisper]",
        "[comment:Personal study only - do not redistribute]",
    ]
    total = duration if duration is not None else (entries[-1][0] + 5.0 if entries else 0.0)
    lines.append(f"[length:{int(total // 60):02d}:{total % 60:05.2f}]")
    lines.extend(f"{fmt_lrc_time(start)}{text}" for start, text in entries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_srt(path: Path, entries: list[tuple[float, str]], duration: float | None) -> Path:
    cues = [(start, text) for start, text in entries if text]
    limit = max(0.0, duration) if duration is not None else None
    starts: list[float] = []
    for start, _text in cues:
        clamped = max(0.0, start)
        if limit is not None:
            clamped = min(clamped, limit)
        if starts:
            clamped = max(clamped, starts[-1])
        starts.append(clamped)

    lines: list[str] = []
    for i, ((_, text), start) in enumerate(zip(cues, starts)):
        if i + 1 < len(starts):
            end = starts[i + 1]
        elif limit is not None:
            end = limit
        else:
            end = start + 5.0
        end = max(start, end)
        lines += [str(i + 1), f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def audio_duration(path: Path) -> float | None:
    try:
        import av
    except ImportError:
        return None
    try:
        with av.open(str(path)) as container:
            if container.duration is not None:
                return container.duration / 1_000_000
    except Exception as exc:  # noqa: BLE001 - probe failure is non-fatal
        print(f"  warn: duration probe failed: {exc}", flush=True)
    return None


def resolve_ffmpeg() -> str | None:
    """Return an ffmpeg executable: NHK_EASY_FFMPEG_PATH first, then PATH."""
    env = os.environ.get("NHK_EASY_FFMPEG_PATH")
    if env and Path(env).is_file():
        return env
    return shutil.which("ffmpeg")


def build_mkv_mux_args(
    ffmpeg_path: str, audio_path: Path, srt_path: Path, out_path: Path
) -> list[str]:
    """Build the ffmpeg command muxing audio + SRT into an MKV for VLC.

    The audio stream is copied unchanged; the subtitle track is embedded as
    SRT (Japanese, default) and a minimal still-video track is synthesized so
    VLC creates the video output that sidecar subtitles for audio-only media
    are otherwise dropped for.
    """
    return [
        ffmpeg_path,
        "-y",
        "-f", "lavfi", "-i", "color=c=0x101418:s=480x360:r=2",
        "-i", str(audio_path),
        "-i", str(srt_path),
        "-map", "1:a",
        "-map", "0:v",
        "-map", "2:s",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-crf", "30", "-r", "2", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-c:s", "srt",
        "-metadata:s:s:0", "language=jpn",
        "-disposition:s:0", "default",
        "-shortest", str(out_path),
    ]


def mux_mkv(article_dir: Path, audio_path: Path) -> Path | None:
    """Write audio.mkv (audio + embedded SRT) next to the sidecar files.

    Returns the MKV path, or None when there is no SRT to embed, ffmpeg is
    unavailable, or the mux fails — all non-fatal for the LRC workflow.
    """
    srt_path = article_dir / "audio.srt"
    if not srt_path.is_file():
        return None
    ffmpeg_path = resolve_ffmpeg()
    if ffmpeg_path is None:
        print("  warn: ffmpeg not found, skipping audio.mkv", flush=True)
        return None
    out_path = article_dir / "audio.mkv"
    args = build_mkv_mux_args(ffmpeg_path, audio_path, srt_path, out_path)
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  warn: audio.mkv mux failed: {exc}", flush=True)
        return None
    if completed.returncode != 0 or not out_path.is_file():
        stderr = (completed.stderr or "").strip().splitlines()
        detail = stderr[-1] if stderr else "no output file"
        print(f"  warn: audio.mkv mux failed: {detail}", flush=True)
        return None
    return out_path


def map_time(
    a_idx: int, a2w: dict[int, int], sorted_a: list[int], char_time: list[float]
) -> float | None:
    """Map an article char index to audio seconds via equal-block neighbours."""
    if a_idx in a2w:
        return char_time[a2w[a_idx]]
    i = bisect.bisect_left(sorted_a, a_idx)
    before = sorted_a[i - 1] if i > 0 else None
    after = sorted_a[i] if i < len(sorted_a) else None
    if before is None and after is None:
        return None
    if before is None:
        return char_time[a2w[after]]  # type: ignore[index]
    if after is None:
        return char_time[a2w[before]]
    t0, t1 = char_time[a2w[before]], char_time[a2w[after]]
    frac = (a_idx - before) / max(1, after - before)
    return t0 + frac * (t1 - t0)


def interpolate_times(times: list[float | None], duration: float | None) -> list[float]:
    """Clamp known times and spread unmatched runs into the available gaps."""
    limit = max(0.0, duration) if duration is not None else None
    result = [None if value is None else max(0.0, value) for value in times]
    if limit is not None:
        result = [None if value is None else min(value, limit) for value in result]

    i = 0
    while i < len(result):
        if result[i] is not None:
            i += 1
            continue
        run_start = i
        while i < len(result) and result[i] is None:
            i += 1
        run_end = i
        count = run_end - run_start
        before = result[run_start - 1] if run_start else None
        after = result[run_end] if run_end < len(result) else None
        low = before if before is not None else 0.0
        if after is not None:
            high = max(low, after)
        elif limit is not None:
            high = max(low, limit)
        else:
            high = low + 0.01 * (count + 1)
        step = (high - low) / (count + 1)
        for offset in range(count):
            result[run_start + offset] = low + step * (offset + 1)

    return [value if value is not None else 0.0 for value in result]


def monotonic_entries(
    units: list[tuple[str, str]], times: list[float], duration: float | None
) -> list[tuple[float, str]]:
    """Clamp timestamps and omit lines that cannot have a unique timestamp."""
    limit = max(0.0, duration) if duration is not None else None
    entries: list[tuple[float, str]] = []
    epsilon = 0.01
    for (_, text), stamp in zip(units, times):
        stamp = max(0.0, stamp)
        if limit is not None:
            stamp = min(stamp, limit)
        if entries and stamp <= entries[-1][0]:
            stamp = entries[-1][0] + epsilon
        if limit is not None and stamp > limit:
            continue
        entries.append((stamp, text))
    return entries


def align(
    article_dir: Path,
    model: object,
    model_name: str,
    *,
    write_srt_file: bool = True,
    make_mkv: bool = True,
) -> Path | None:
    article_json = article_dir / "article.json"
    if not article_json.exists():
        print(f"skip {article_dir.name}: missing article.json", flush=True)
        return None

    data = json.loads(article_json.read_text(encoding="utf-8"))
    audio_path = find_audio_path(article_dir, data)
    if audio_path is None:
        print(f"skip {article_dir.name}: missing audio.m4a or audio.mp3", flush=True)
        return None
    article_id = str(data.get("article_id", ""))
    title = (data.get("title") or {}).get("plain", "")
    paras = [p.get("plain", "") for p in data.get("paragraphs", []) if p.get("plain")]
    if not paras:
        print(f"skip {article_dir.name}: no paragraphs", flush=True)
        return None

    units: list[tuple[str, str]] = []  # (kind, display_text)
    if title.strip():
        units.append(("title", title.strip()))
    for para in paras:
        units.extend(("body", sent) for sent in split_sentences(para))

    norm_units = [normalize(text) for _, text in units]
    article_stream = "".join(norm_units)
    unit_spans: list[tuple[int, int]] = []
    pos = 0
    for norm in norm_units:
        unit_spans.append((pos, pos + len(norm)))
        pos += len(norm)

    print(f"transcribing {audio_path.name} [{model_name}] ...", flush=True)
    duration = audio_duration(audio_path)
    segments, info = model.transcribe(  # type: ignore[attr-defined]
        str(audio_path), language="ja", beam_size=5, word_timestamps=True, vad_filter=True
    )
    if duration is None:
        info_duration = getattr(info, "duration", None)
        if isinstance(info_duration, (int, float)) and info_duration >= 0:
            duration = float(info_duration)
    words: list[tuple[float, float, str]] = []
    for seg in segments:
        for word in seg.words or []:
            norm_word = normalize(word.word)
            if norm_word:
                words.append((word.start, word.end, norm_word))
    if not words:
        print(f"  FAIL {article_dir.name}: no words transcribed", flush=True)
        return None

    whisper_stream = "".join(norm for _, _, norm in words)
    char_time: list[float] = []
    for start, end, norm in words:
        span = max(end - start, 0.0)
        char_time.extend(start + span * i / len(norm) for i in range(len(norm)))

    a2w: dict[int, int] = {}
    matched = 0
    matcher = difflib.SequenceMatcher(None, whisper_stream, article_stream, autojunk=False)
    for tag, i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            matched += j2 - j1
            for k in range(j2 - j1):
                a2w[j1 + k] = i1 + k
    coverage = matched / max(1, len(article_stream))
    print(f"  coverage: {coverage:.0%} ({len(words)} words)", flush=True)
    if coverage < 0.30:
        print(f"  FAIL {article_dir.name}: coverage {coverage:.0%} too low, skipping", flush=True)
        return None
    sorted_a = sorted(a2w)
    times: list[float | None] = [
        map_time(start, a2w, sorted_a, char_time) for start, _ in unit_spans
    ]

    # EASY audio normally skips the title; keep it only if it aligns near the start.
    if units and units[0][0] == "title":
        span_start, span_end = unit_spans[0]
        matched_chars = sum(1 for k in range(span_start, span_end) if k in a2w)
        ratio = matched_chars / max(1, span_end - span_start)
        if times[0] is None or times[0] > 20.0 or ratio < 0.5:
            print("  note: title not read in audio, dropping title line", flush=True)
            units.pop(0)
            times.pop(0)
            unit_spans.pop(0)

    missing = [i for i, stamp in enumerate(times) if stamp is None]
    filled_times = interpolate_times(times, duration)
    for i in missing:
        print(f"  warn: line {i} unaligned, interpolated t={filled_times[i]:.2f}s", flush=True)

    entries = monotonic_entries(units, filled_times, duration)
    if len(entries) < len(units):
        print("  warn: dropped lines without room for unique timestamps", flush=True)
    if not entries:
        print(f"  FAIL {article_dir.name}: no aligned lines", flush=True)
        return None
    out = write_lrc(article_dir / "audio.lrc", title, article_id, entries, duration)
    if write_srt_file:
        write_srt(article_dir / "audio.srt", entries, duration)
    if make_mkv:
        if mux_mkv(article_dir, audio_path) is not None:
            print("  wrote audio.mkv: embedded subtitles for VLC", flush=True)
    first = entries[0][0] if entries else 0.0
    last = entries[-1][0] if entries else 0.0
    extra = f" duration={duration:.1f}s" if duration is not None else ""
    print(
        f"  wrote {out.name}: {len(entries)} lines, first={first:.2f}s last={last:.2f}s{extra}",
        flush=True,
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate .lrc lyrics for fetched articles.")
    parser.add_argument("path", help="Article dir or output tree root to search")
    parser.add_argument("--model", default="small", help="faster-whisper model (default: small)")
    parser.add_argument(
        "--srt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also write audio.srt for players without LRC support (default: on)",
    )
    parser.add_argument(
        "--mkv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Also mux audio.mkv (audio + embedded SRT + still video) for VLC, "
            "which drops sidecar subtitles for audio-only files (default: on)"
        ),
    )
    args = parser.parse_args(argv)

    if os.name == "nt":
        # Work around `mkl_malloc: failed to allocate memory` when loading the
        # model on some Windows machines. Explicit user settings take precedence.
        # Must be set before MKL loads (i.e. before importing faster_whisper).
        os.environ.setdefault("MKL_NUM_THREADS", "2")
        os.environ.setdefault("OMP_NUM_THREADS", "2")
        os.environ.setdefault("KMP_AFFINITY", "disabled")

    root = expand_path(args.path)
    targets = discover_targets(root)
    if not targets:
        print(f"no articles with audio found under {root}", file=sys.stderr)
        return 2
    print(f"found {len(targets)} article(s) with audio", flush=True)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper is not installed. Run: pip install -e '.[lrc]'", file=sys.stderr)
        return 2

    print(f"loading whisper model '{args.model}' (first run downloads it) ...", flush=True)
    try:
        model = WhisperModel(args.model, device="cpu", compute_type="int8")
    except Exception as exc:  # noqa: BLE001 - native model loaders raise varied errors
        print(f"failed to load whisper model: {exc}", file=sys.stderr)
        print(
            "If this mentions mkl_malloc, limit threads with MKL_NUM_THREADS=2 "
            "and OMP_NUM_THREADS=2, or try --model base.",
            file=sys.stderr,
        )
        return 1
    done = 0
    for article_json in targets:
        try:
            if align(
                article_json.parent,
                model,
                args.model,
                write_srt_file=args.srt,
                make_mkv=args.mkv,
            ):
                done += 1
        except Exception as exc:  # noqa: BLE001 - report per-article and continue
            print(f"  FAIL {article_json.parent.name}: {exc}", flush=True)
    print(f"done: {done}/{len(targets)} LRC written", flush=True)
    return 0 if done == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
