# nhk-easy-fetcher

> Unofficial, local-first CLI for fetching NHK NEWS WEB EASY articles for personal Japanese study.

`nhk-easy-fetcher` discovers articles from the public NHK EASY sitemap, fetches article pages, parses classic EASY HTML when available, and exports JSON, Markdown, and plain text to a local output tree.

**NHK content is for personal study only — do not redistribute.**

## Project status

v0.1 vertical slice (this release):

- sitemap-based article discovery (legacy `ne…` and current `YYYYMMDDde…` article IDs);
- page-mode detection (`classic_complete` vs `next_partial`);
- ruby-aware parsing and multi-format export;
- optional HLS audio via `top-list.json` / page metadata (`--audio m4a|mp3|manifest`);
- SQLite deduplication and atomic writes;
- `CookieJarProvider` for user-owned NHK ONE session cookies;
- offline test suite with synthetic fixtures.
- optional Anki deck export from saved articles (`export-anki`).

Full article text requires a valid NHK ONE authorization session. Anonymous fetches return the NHK ONE consent shell (no `#js-article-body`) and exit with code **3** (`authorization_required`).

## Quick start

```console
# Install (editable dev install)
pip install -e '.[dev]'

# Fetch the newest article from the sitemap (anonymous — will report auth required)
nhk-easy fetch-latest --output ~/NHK-Easy

# Dry-run: discover without writing files
nhk-easy fetch --latest --dry-run --output ~/NHK-Easy

# With browser cookies (after completing NHK ONE consent in your browser)
nhk-easy fetch-latest \
  --auth cookie_jar \
  --cookie-jar ~/.nhk-easy-fetcher/auth/cookies.json \
  --output ~/NHK-Easy

# With audio (requires auth + ffmpeg; see Audio section below)
nhk-easy fetch-latest \
  --auth cookie_jar \
  --cookie-jar ~/.nhk-easy-fetcher/auth/cookies.json \
  --audio m4a \
  --output ~/NHK-Easy

# Offline probe (fixtures only)
nhk-easy probe

# Export saved articles to an Anki deck (personal study only)
pip install -e '.[anki]'   # once, for APKG support
nhk-easy export-anki --output ~/NHK-Easy --deck nhk-easy
nhk-easy export-anki ~/NHK-Easy/articles/2026/2026-09/2026-09-05_ne2026090512345 --include-audio

# Live structural probe (opt-in; no content saved)
NHK_EASY_LIVE_TEST=1 nhk-easy probe --live --i-understand-live-requests --json
```

### One-click latest (text + audio)

```console
./scripts/fetch-latest.sh
OUTPUT_DIR=~/NHK-Easy AUDIO_MODE=m4a COOKIE_JAR=~/.nhk-easy-fetcher/auth/cookies.json ./scripts/fetch-latest.sh
```

The script checks `ffmpeg`, installs the package if needed, guides you to
complete NHK ONE consent once in your own browser, enforces `chmod 600` on the
cookie jar, then runs `fetch-latest --audio m4a`.

首次运行会自动弹起本机 Chrome 供你点一次同意，回车即自动保存 cookie（需 `pip install -e '.[browser]'`，不下载新浏览器，不碰日常 profile）。

### Cookie jar format

Save a permission-restricted file (`chmod 600` on POSIX; on Windows the
file relies on your user-profile ACLs, so keep it under your home
directory and out of shared folders):

```json
{
  "cookies": {
    "cookie_name": "cookie_value"
  },
  "headers": {
    "Authorization": "Bearer …"
  }
}
```

Export cookies from your browser after completing NHK ONE “ご利用にあたって” consent on [news.web.nhk](https://news.web.nhk/news/easy/). Do not commit this file.

## Audio

Voice URIs are resolved from article HTML when present, otherwise from authorized
[`top-list.json`](https://news.web.nhk/news/easy/top-list.json) (`news_easy_voice_uri` field).

HLS manifests are built as:

```text
https://media.vd.st.nhk/news/easy_audio/{stem}/index.m3u8
```

where `{stem}` is `news_easy_voice_uri` with its file extension removed.

```console
nhk-easy fetch-latest --audio off      # text only (default)
nhk-easy fetch-latest --audio m4a      # AAC in M4A via ffmpeg re-encode
nhk-easy fetch-latest --audio mp3      # MP3 via libmp3lame
nhk-easy fetch-latest --audio manifest # save manifest only
```

### CDN access and Akamai tokens

The bare HLS CDN URL (`media.vd.st.nhk`) returns **HTTP 403** without a valid Akamai
`hdnts` query token. The fetcher mints this token via
`https://mediatoken.web.nhk/v1/token` using `Authorization: Bearer {z_at}` from your
cookie jar, then passes the **remote** tokenized manifest URL directly to `ffmpeg -i`.

ffmpeg uses **re-encode** (`-c:a aac -b:a 64k` for M4A, `libmp3lame` for MP3), not
`-c copy`, because EASY audio is HE-AAC. Local manifest files (if used) are rewritten
with `hdnts` on segment lines and opened with
`-protocol_whitelist file,http,https,tcp,tls,crypto`.

## Synced lyrics (LRC)

`scripts/make_lrc.py` aligns `article.json` sentences against `audio.m4a`
with faster-whisper word timestamps and writes `audio.lrc` next to the audio
file (same basename, so lyric-capable players auto-load it).

```console
pip install -e '.[lrc]'
python scripts/make_lrc.py ~/NHK-Easy/articles
python scripts/make_lrc.py ~/NHK-Easy/articles/2026/2026-09/2026-09-07_20260907de48812
```

Copy `audio.m4a` + `audio.lrc` to your phone and open them in Musicolet
or AIMP for Spotify-style scrolling lyrics. An `audio.srt` with the
same timestamps is written alongside for players with SRT support
(in VLC use Subtitle > Add Subtitle File; same-basename auto-load is
best-effort, and VLC does not support `.lrc`). `--no-srt` to skip. EASY audio normally skips
the title, so the script drops the title line when it does not align with
the audio.

On some Windows machines the model fails to load with
`mkl_malloc: failed to allocate memory`; the script already limits its own
thread pools, and you can also fall back to a smaller model:

```console
set MKL_NUM_THREADS=2
set OMP_NUM_THREADS=2
python scripts/make_lrc.py ~/NHK-Easy/articles --model base
```

Generated lyrics inherit the article's content rights — personal study only,
do not redistribute.

## Output layout

```text
~/NHK-Easy/
├── .nhk-easy-fetcher/state.sqlite3
└── articles/
    └── 2026/2026-09/2026-09-05_ne2026090512345/
        ├── article.json
        ├── article.md
        ├── article.txt
        ├── audio.m4a               # when --audio m4a succeeds
        ├── audio.lrc               # when scripts/make_lrc.py succeeds
        ├── audio.srt               # alongside audio.lrc unless --no-srt
        └── checksums.sha256
```

## CLI commands

| Command | Description |
| --- | --- |
| `nhk-easy fetch --latest` | Fetch newest sitemap article |
| `nhk-easy fetch-latest` | Alias for `fetch --latest` (always 1 article) |
| `nhk-easy fetch --latest --max-articles 10` | Fetch 10 newest articles |
| `nhk-easy fetch --date today` | Fetch articles for a date (`today` or `YYYY-MM-DD`) |
| `nhk-easy fetch --since 2026-09-01 --until 2026-09-05` | Fetch a date range (both ends inclusive) |
| `nhk-easy fetch --since 2026-09-01` | Fetch everything from a date onward |
| `nhk-easy fetch --audio m4a` | Download audio (needs auth + ffmpeg) |
| `nhk-easy fetch --output /path/to/dir` | Write to a custom directory (default `~/NHK-Easy`) |
| `nhk-easy fetch --dry-run` | Discover without writing files |
| `nhk-easy status --output PATH` | Show local SQLite state |
| `nhk-easy verify [ARTICLE_DIR] [--output PATH] [--audio]` | Validate checksums (optional ffprobe for audio) |
| `nhk-easy cleanup [--output PATH] [--older-than DAYS] [--dry-run]` | Remove stale `.partial` temp files |
| `nhk-easy probe` | Offline fixture probe |
| `nhk-easy probe --live` | Live sitemap + one-page structural probe |
| `nhk-easy export-anki` | Export saved complete articles to `.apkg` (alias: `anki`) |

`export-anki` reads local `article.json` files only. Decks are written under `{output}/.nhk-easy-fetcher/anki/` unless `--output` ends in `.apkg`. Requires `pip install -e '.[anki]'`. NHK content in decks is for personal study — do not redistribute.

`--latest`, `--date`, and `--since`/`--until` select distinct modes and cannot
be combined. `--max-articles` must be positive and
caps any selection (latest, date, or range). Already-complete articles are
skipped, so re-running a range only fetches what is missing.

```console
# Newest article with audio; first capture opens Chrome once for consent
./scripts/fetch-latest.sh
OUTPUT_DIR=~/NHK-Easy AUDIO_MODE=m4a ./scripts/fetch-latest.sh

# Same via CLI (cookie jar already saved)
nhk-easy fetch --latest --max-articles 10 --audio m4a --output ~/NHK-Easy

# A date range with audio to a custom directory
nhk-easy fetch --since 2026-09-01 --until 2026-09-05 \
  --audio m4a --output /path/to/dir
```

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success or skipped |
| 1 | Retryable run error |
| 2 | Usage/config error |
| 3 | Authorization required |
| 4 | Source contract changed |
| 5 | Partial success |

## Development

```console
pip install -e '.[dev]'
pytest -q
ruff check .
mypy src
```

## Documentation

- [Development specification](docs/development.md)
- [Operations / scheduling guide](docs/operations.md)
- [Implementation plan](docs/plans/2026-09-05-nhk-easy-fetcher-implementation.md)
- [Anki export design](docs/plans/2026-09-06-anki-export.md)
- [Reference notes](docs/references.md)
- [Live contract verification (redacted)](docs/verification/2026-09-05-live-contract.md)

## License and content rights

Project code and documentation are [MIT licensed](LICENSE). NHK articles, audio, images, and metadata are **not** covered by that license.
