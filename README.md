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

# Live structural probe (opt-in; no content saved)
NHK_EASY_LIVE_TEST=1 nhk-easy probe --live --i-understand-live-requests --json
```

### Cookie jar format

Save a permission-restricted file (`chmod 600`):

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
        └── checksums.sha256
```

## CLI commands

| Command | Description |
| --- | --- |
| `nhk-easy fetch --latest` | Fetch newest sitemap article |
| `nhk-easy fetch-latest` | Alias for `fetch --latest` |
| `nhk-easy fetch --date today` | Fetch articles for a date |
| `nhk-easy fetch --audio m4a` | Download audio (needs auth + ffmpeg) |
| `nhk-easy status --output PATH` | Show local SQLite state |
| `nhk-easy probe` | Offline fixture probe |
| `nhk-easy probe --live` | Live sitemap + one-page structural probe |

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
- [Implementation plan](docs/plans/2026-09-05-nhk-easy-fetcher-implementation.md)
- [Reference notes](docs/references.md)
- [Live contract verification (redacted)](docs/verification/2026-09-05-live-contract.md)

## License and content rights

Project code and documentation are [MIT licensed](LICENSE). NHK articles, audio, images, and metadata are **not** covered by that license.
