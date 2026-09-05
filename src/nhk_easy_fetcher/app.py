"""Application orchestration for fetch runs."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from nhk_easy_fetcher.audio import (
    AudioDownloadResult,
    authorize_manifest_url,
    download_audio,
    resolve_hls_url,
)
from nhk_easy_fetcher.auth import AuthProvider, CookieJarProvider, NoAuthProvider
from nhk_easy_fetcher.client import HttpClient
from nhk_easy_fetcher.config import AppConfig
from nhk_easy_fetcher.discovery import discover_article_urls
from nhk_easy_fetcher.errors import (
    AudioUnavailable,
    AuthorizationUnavailable,
    FullContentUnavailable,
)
from nhk_easy_fetcher.metadata import fetch_top_list_voice_map, resolve_voice_uri
from nhk_easy_fetcher.models import AudioInfo, DiscoveredArticle, FetchResult, RunSummary
from nhk_easy_fetcher.page_mode import detect_page_mode
from nhk_easy_fetcher.parser import parse_complete_article
from nhk_easy_fetcher.storage import StateStore


class FetchApplication:
    def __init__(
        self,
        config: AppConfig,
        *,
        auth_provider: AuthProvider | None = None,
        store: StateStore | None = None,
    ) -> None:
        self.config = config
        self.auth_provider = auth_provider or self._build_auth_provider()
        self._store = store

    @property
    def store(self) -> StateStore:
        if self._store is None:
            self._store = StateStore(self.config.storage.output_dir)
        return self._store

    def _build_auth_provider(self) -> AuthProvider:
        if self.config.auth.provider == "cookie_jar":
            return CookieJarProvider(self.config.auth.cookie_jar_path)
        return NoAuthProvider()

    def _formats(self, format_str: str) -> set[str]:
        return {part.strip() for part in format_str.split(",") if part.strip()}

    def _artifact_kinds(self, text_formats: set[str]) -> set[str]:
        kinds = set(text_formats)
        audio_mode = self.config.audio.mode
        if audio_mode == "m4a":
            kinds.add("audio_m4a")
        elif audio_mode == "mp3":
            kinds.add("audio_mp3")
        elif audio_mode == "manifest":
            kinds.add("audio_manifest")
        return kinds

    def discover_from_sitemap(self, client: HttpClient) -> list[DiscoveredArticle]:
        xml = client.get_text(self.config.discovery.sitemap_url)
        return discover_article_urls(xml, tz=self.config.fetch.timezone)

    def fetch_latest(
        self,
        *,
        max_articles: int = 1,
        formats: str = "markdown,json,text",
        dry_run: bool = False,
        force: bool = False,
    ) -> RunSummary:
        return self.fetch(
            latest=True,
            max_articles=max_articles,
            formats=formats,
            dry_run=dry_run,
            force=force,
        )

    def fetch(
        self,
        *,
        target_date: date | None = None,
        latest: bool = False,
        max_articles: int = 1,
        formats: str = "markdown,json,text",
        dry_run: bool = False,
        force: bool = False,
    ) -> RunSummary:
        summary = RunSummary()
        requested_formats = self._formats(formats)
        artifact_kinds = self._artifact_kinds(requested_formats)
        now = datetime.now(tz=ZoneInfo("UTC"))

        try:
            session = self.auth_provider.get_session(now)
        except AuthorizationUnavailable as exc:
            summary.add(
                FetchResult(
                    article_id="",
                    source_url="",
                    status="authorization_required",
                    message=str(exc),
                )
            )
            return summary

        with HttpClient(
            self.config.fetch,
            cookies=session.cookies,
            headers=session.headers,
        ) as client:
            top_list_map: dict[str, str] | None = None
            if self.config.audio.mode != "off":
                try:
                    top_list_map = fetch_top_list_voice_map(client)
                except AuthorizationUnavailable:
                    top_list_map = None

            if dry_run:
                candidates = self.discover_from_sitemap(client)
                if target_date is not None:
                    candidates = [
                        c
                        for c in candidates
                        if c.published_date and c.published_date.date() == target_date
                    ]
                candidates = candidates[:max_articles]

                for candidate in candidates:
                    summary.add(
                        FetchResult(
                            article_id=candidate.article_id,
                            source_url=candidate.source_url,
                            status="skipped",
                            message="dry-run",
                        )
                    )
                return summary

            candidates = self.discover_from_sitemap(client)
            if target_date is not None:
                candidates = [
                    c
                    for c in candidates
                    if c.published_date and c.published_date.date() == target_date
                ]
            candidates = candidates[:max_articles]

            if not candidates:
                summary.add(
                    FetchResult(
                        article_id="",
                        source_url="",
                        status="failed",
                        message="No articles found for the requested criteria",
                    )
                )
                return summary

            for candidate in candidates:
                needs_fetch = self.store.should_fetch(candidate.article_id, artifact_kinds)
                if not force and not needs_fetch:
                    summary.add(
                        FetchResult(
                            article_id=candidate.article_id,
                            source_url=candidate.source_url,
                            status="skipped",
                            message="already complete",
                        )
                    )
                    continue

                result = self._fetch_one(
                    client,
                    candidate.source_url,
                    candidate.article_id,
                    requested_formats,
                    top_list_map=top_list_map,
                    cookies=session.cookies,
                )
                summary.add(result)

        return summary

    def _fetch_one(
        self,
        client: HttpClient,
        source_url: str,
        article_id: str,
        formats: set[str],
        *,
        top_list_map: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> FetchResult:
        audio_failed = False
        try:
            html = client.get_text(source_url)
            page_mode = detect_page_mode(html)

            if page_mode.value != "classic_complete":
                if self.config.fetch.allow_partial:
                    return FetchResult(
                        article_id=article_id,
                        source_url=source_url,
                        status="partial",
                        message=(
                            f"Page mode is {page_mode.value}; full body unavailable. "
                            "Provide a cookie jar (--auth cookie_jar) with NHK ONE consent."
                        ),
                    )
                return FetchResult(
                    article_id=article_id,
                    source_url=source_url,
                    status="authorization_required",
                    message=(
                        f"Page mode is {page_mode.value} (missing #js-article-body). "
                        "NHK ONE authorization is required. Export browser cookies after "
                        "completing consent and set auth.provider=cookie_jar in config."
                    ),
                )

            article = parse_complete_article(
                html,
                source_url=source_url,
                fetched_at=datetime.now(tz=ZoneInfo("UTC")),
                tz=self.config.fetch.timezone,
            )

            audio_result: AudioDownloadResult | None = None
            audio_error: str | None = None
            if self.config.audio.mode != "off":
                voice_uri = resolve_voice_uri(
                    article_id=article_id,
                    html=html,
                    top_list_map=top_list_map,
                )
                if voice_uri is None and top_list_map is None:
                    try:
                        top_list_map = fetch_top_list_voice_map(client)
                        voice_uri = resolve_voice_uri(
                            article_id=article_id,
                            html=html,
                            top_list_map=top_list_map,
                        )
                    except AuthorizationUnavailable:
                        pass

                if voice_uri is None:
                    audio_error = "news_easy_voice_uri not found in HTML or top-list.json"
                    article.audio = AudioInfo(status="unavailable")
                else:
                    try:
                        out_dir = self.store.article_dir(article.article_id, article.published_at)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        if self.config.audio.mode == "manifest":
                            manifest_url = authorize_manifest_url(
                                resolve_hls_url(voice_uri),
                                cookies or {},
                            )
                            manifest_body = client.get_text(manifest_url)
                            from nhk_easy_fetcher.audio import save_manifest_placeholder

                            path = save_manifest_placeholder(out_dir, manifest_url, manifest_body)
                            audio_result = AudioDownloadResult(
                                output_path=path,
                                manifest_url=manifest_url,
                                format="manifest",
                            )
                        else:
                            audio_result = download_audio(
                                news_easy_voice_uri=voice_uri,
                                output_dir=out_dir,
                                mode=self.config.audio.mode,
                                ffmpeg_path=self.config.audio.ffmpeg_path,
                                cookies=cookies or {},
                            )
                        article.audio = AudioInfo(
                            status="available",
                            format=audio_result.format,
                            manifest_url=audio_result.manifest_url,
                        )
                    except AudioUnavailable as exc:
                        audio_error = str(exc)
                        article.audio = AudioInfo(
                            status="unavailable",
                            manifest_url=authorize_manifest_url(
                                resolve_hls_url(voice_uri),
                                cookies or {},
                            ),
                        )
                        audio_failed = True

            out_dir = self.store.save_complete(
                article,
                formats,
                audio_result=audio_result,
            )
            if audio_failed:
                return FetchResult(
                    article_id=article_id,
                    source_url=source_url,
                    status="partial",
                    message=f"text exported; audio unavailable: {audio_error}",
                    output_dir=str(out_dir),
                )
            return FetchResult(
                article_id=article_id,
                source_url=source_url,
                status="completed",
                message="exported",
                output_dir=str(out_dir),
            )
        except AuthorizationUnavailable as exc:
            self.auth_provider.invalidate(str(exc))
            return FetchResult(
                article_id=article_id,
                source_url=source_url,
                status="authorization_required",
                message=str(exc),
            )
        except FullContentUnavailable as exc:
            return FetchResult(
                article_id=article_id,
                source_url=source_url,
                status="authorization_required",
                message=str(exc),
            )
        except Exception as exc:
            return FetchResult(
                article_id=article_id,
                source_url=source_url,
                status="failed",
                message=str(exc),
            )
