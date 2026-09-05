# Live contract verification (2026-09-05)

- Date/time (UTC): 2026-09-05T01:20Z
- Sitemap status/content type: HTTP 200, `application/xml`
- Article URL redacted identifier: `ne2025090516408`
- Page mode: `next_partial`
- Selector presence (`.article-title`, `.article-date`, `#js-article-body`): false / false / false
- Authorization mode: none (anonymous fetch)
- Audio metadata availability: not tested without hdnts token (bare CDN returns 403)
- HLS base URL verified: `https://media.vd.st.nhk/news/easy_audio/{stem}/index.m3u8`
- Result: Sitemap discovery works anonymously. Article pages return NHK ONE consent shell without `#js-article-body`. Full export requires `cookie_jar` auth with NHK ONE consent cookies. Audio CDN requires Akamai `hdnts` token (TODO: mint from `z_at`).
