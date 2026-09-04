# nhk-easy-fetcher

> Unofficial, local-first CLI and scheduler for fetching NHK NEWS WEB EASY articles and audio for personal Japanese study.

`nhk-easy-fetcher` is a documentation-first project. The initial commit defines a small, testable Python implementation that can discover current NHK NEWS WEB EASY articles, save a personal-study copy in useful formats, and optionally download the article audio when it is made available by NHK.

## Project status

No production fetcher has been implemented yet. The intended v0 is deliberately narrow:

- discover current article URLs from the public EASY sitemap;
- obtain full article HTML only through a valid, browser-compatible NHK ONE authorization state when NHK requires it;
- export an article as JSON, Markdown, plain text, and optionally source HTML;
- resolve and remux HLS audio to M4A with `ffmpeg` without unnecessary transcoding;
- keep local cache and deduplication state, and run safely from a local scheduler.

It is **not** a hosted mirror, bulk archive, public content API, account-automation tool, or a way to bypass NHK access controls.

## Documentation

- [Complete development specification](docs/development.md) — goals, architecture, current source behavior, formats, CLI, operations, tests, and legal boundaries.
- [Implementation plan](docs/plans/2026-09-05-nhk-easy-fetcher-implementation.md) — small, test-first tasks for the first usable release.
- [Research notes and reference projects](docs/references.md) — source projects, observed endpoints, and what to borrow (or avoid).

## Key design choices

1. **Sitemap first.** The public sitemap is the primary discovery source. The unauthenticated `top-list.json` endpoint is not treated as a stable public API.
2. **Authorization is an adapter.** NHK ONE's anonymous authorization state is volatile and undocumented; it is isolated behind `AuthProvider`, never hard-coded into all fetches, and never treated as permission to evade access restrictions.
3. **Full text must be honest.** A fetch is marked `complete` only when the classic EASY body (`#js-article-body`) is present. A truncated Next.js page is not silently exported as a complete article.
4. **Local-first and copyright-aware.** Fetched NHK material stays outside this repository and is never committed, released, or published by default.

## Intended quick start (after v0 implementation)

```console
$ pipx install nhk-easy-fetcher
$ nhk-easy fetch --date today --audio --format markdown,json --output ~/NHK-Easy
$ nhk-easy status --output ~/NHK-Easy
```

The commands above describe the planned interface; they are not available in this initial documentation release.

## License and content rights

The project code and documentation are [MIT licensed](LICENSE). NHK articles, audio, images, and metadata are **not** covered by that license. Users must review the applicable NHK terms and copyright notices, keep use personal and lawful, respect rate limits and geographic/service restrictions, and must not redistribute downloaded content.
