"""Generate synced-lyrics files for fetched NHK EASY articles.

Aligns each article's ``article.json`` sentences against its ``audio.m4a``
with faster-whisper word timestamps, then writes ``audio.lrc`` next to the
audio file (same basename, so lyric-capable players auto-load it) plus
``audio.srt`` for players without LRC support such as VLC
(``--no-srt`` to skip).

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
import sys
import unicodedata
from pathlib import Path

_SENT_RE = re.compile(r"[^。！？]*[。！？][」』）\"”’]*|[^。！？]+$")
_STRIP_RE = re.compile(
    r"[\s、。，．・「」『』（）()［\]<>〈〉《》…‥—―～~"
    r"!！?？:：;；,，.．\"“”'‘’/／＼\\|｜+＋*＊#＃%％&＆@＠·]+"
)


def normalize(text: str) -> str:
    """NFKC-normalize and drop whitespace/punctuation for fuzzy matching."""
    return _STRIP_RE.sub("", unicodedata.normalize("NFKC", text))


def split_sentences(text: str) -> list[str]:
    """Split Japanese text into sentences, keeping closing brackets."""
    return [m.group(0) for m in _SENT_RE.finditer(text.strip()) if m.group(0).strip()]


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
    ]
    total = duration if duration else (entries[-1][0] + 5.0 if entries else 0.0)
    lines.append(f"[length:{int(total // 60):02d}:{total % 60:05.2f}]")
    lines.extend(f"{fmt_lrc_time(start)}{text}" for start, text in entries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_srt(path: Path, entries: list[tuple[float, str]], duration: float | None) -> Path:
    lines: list[str] = []
    shown = 0
    for i, (start, text) in enumerate(entries):
        if not text:
            continue
        if i + 1 < len(entries):
            end = entries[i + 1][0]
        elif duration is not None:
            end = duration
        else:
            end = start + 5.0
        end = max(end, start + 1.0)
        if duration is not None and duration >= start + 1.0:
            end = min(end, duration)
        shown += 1
        lines += [str(shown), f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}", text, ""]
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


def align(
    article_dir: Path, model: object, model_name: str, *, write_srt_file: bool = True
) -> Path | None:
    article_json = article_dir / "article.json"
    audio_path = article_dir / "audio.m4a"
    if not article_json.exists() or not audio_path.exists():
        print(f"skip {article_dir.name}: missing article.json or audio.m4a", flush=True)
        return None

    data = json.loads(article_json.read_text(encoding="utf-8"))
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
    segments, _info = model.transcribe(  # type: ignore[attr-defined]
        str(audio_path), language="ja", beam_size=5, word_timestamps=True, vad_filter=True
    )
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

    # Interpolate any sentence that failed to align between its neighbours.
    count = len(units)
    for i in range(count):
        if times[i] is not None:
            continue
        prev_idx = max((j for j in range(i) if times[j] is not None), default=None)
        next_idx = min((j for j in range(i + 1, count) if times[j] is not None), default=None)
        prev = times[prev_idx] if prev_idx is not None else None
        nxt = times[next_idx] if next_idx is not None else None
        if prev is not None and nxt is not None and next_idx is not None and prev_idx is not None:
            frac = (i - prev_idx) / (next_idx - prev_idx)
            times[i] = prev + (nxt - prev) * frac
        elif prev is not None:
            times[i] = prev + 2.0
        elif nxt is not None:
            times[i] = max(0.0, nxt - 2.0)
        else:
            times[i] = 0.0
        print(f"  warn: line {i} unaligned, interpolated t={times[i]:.2f}s", flush=True)

    for i in range(1, count):  # enforce monotonicity
        cur, prev = times[i], times[i - 1]
        if cur is not None and prev is not None and cur < prev:
            times[i] = prev

    duration = audio_duration(audio_path)
    entries: list[tuple[float, str]] = []
    for (_, text), stamp in zip(units, times):
        assert stamp is not None, "interpolation must fill every timestamp"
        entries.append((stamp, text))
    if not entries:
        print(f"  FAIL {article_dir.name}: no aligned lines", flush=True)
        return None
    out = write_lrc(article_dir / "audio.lrc", title, article_id, entries, duration)
    if write_srt_file:
        write_srt(article_dir / "audio.srt", entries, duration)
    first = entries[0][0] if entries else 0.0
    last = entries[-1][0] if entries else 0.0
    extra = f" duration={duration:.1f}s" if duration else ""
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
    args = parser.parse_args(argv)

    if os.name == "nt":
        # Work around `mkl_malloc: failed to allocate memory` when loading the
        # model on some Windows machines. Explicit user settings take precedence.
        # Must be set before MKL loads (i.e. before importing faster_whisper).
        os.environ.setdefault("MKL_NUM_THREADS", "2")
        os.environ.setdefault("OMP_NUM_THREADS", "2")
        os.environ.setdefault("KMP_AFFINITY", "disabled")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper is not installed. Run: pip install -e '.[lrc]'", file=sys.stderr)
        return 2

    print(f"loading whisper model '{args.model}' (first run downloads it) ...", flush=True)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    root = Path(args.path)
    if (root / "article.json").exists():
        targets = [root / "article.json"]
    else:
        targets = sorted(p for p in root.rglob("article.json") if (p.parent / "audio.m4a").exists())
    if not targets:
        print(f"no articles with audio found under {root}", file=sys.stderr)
        return 2
    print(f"found {len(targets)} article(s) with audio", flush=True)
    done = 0
    for article_json in targets:
        try:
            if align(article_json.parent, model, args.model, write_srt_file=args.srt):
                done += 1
        except Exception as exc:  # noqa: BLE001 - report per-article and continue
            print(f"  FAIL {article_json.parent.name}: {exc}", flush=True)
    print(f"done: {done}/{len(targets)} LRC written", flush=True)
    return 0 if done == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
