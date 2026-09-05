"""Application orchestration for fetch runs."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from nhk_easy_fetcher.auth import AuthProvider, CookieJarProvider, NoAuthProvider
from nhk_easy_fetcher.client import HttpClient
from nhk_easy_fetcher.config import AppConfig
from nhk_easy_fetcher.discovery import discover_article_urls
from nhk_easy_fetcher.errors import AuthorizationUnavailable, FullContentUnavailable
from nhk_easy_fetcher.models import DiscoveredArticle, FetchResult, RunSummary
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
            if dry_run:
                candidates = self.discover_from_sitemap(client)
                if target_date is not None:
                    candidates = [
                        c
                        for c in candidates
                        if c.published_date
                        and c.published_date.date() == target_date
                    ]
                if latest:
                    candidates = candidates[:max_articles]
                elif target_date is not None:
                    candidates = candidates[:max_articles]
                else:
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
                needs_fetch = self.store.should_fetch(candidate.article_id, requested_formats)
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
                )
                summary.add(result)

        return summary

    def _fetch_one(
        self,
        client: HttpClient,
        source_url: str,
        article_id: str,
        formats: set[str],
    ) -> FetchResult:
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
            out_dir = self.store.save_complete(article, formats)
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
