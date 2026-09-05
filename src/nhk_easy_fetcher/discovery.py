"""Discover article URLs from the NHK EASY sitemap."""

from __future__ import annotations

from datetime import date, datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from nhk_easy_fetcher.article_ids import ARTICLE_URL_RE, article_id_to_date
from nhk_easy_fetcher.models import DiscoveredArticle


def in_date_range(
    published: datetime | None,
    since: date | None,
    until: date | None,
) -> bool:
    """Check an article's published datetime against an inclusive date range.

    Open ends (None) do not filter. An unknown published date only passes
    when no range is active.
    """
    if since is None and until is None:
        return True
    if published is None:
        return False
    day = published.date()
    if since is not None and day < since:
        return False
    return not (until is not None and day > until)


def discover_article_urls(
    sitemap_xml: str,
    *,
    only_date: date | None = None,
    tz: str = "Asia/Tokyo",
) -> list[DiscoveredArticle]:
    root = ElementTree.fromstring(sitemap_xml)
    articles: list[DiscoveredArticle] = []
    seen: set[str] = set()

    for loc in root.findall(".//{*}loc"):
        url = (loc.text or "").strip()
        match = ARTICLE_URL_RE.search(url)
        if not match:
            continue
        article_id = match.group("id")
        if article_id in seen:
            continue
        seen.add(article_id)

        published_date: datetime | None = None
        article_date = article_id_to_date(article_id)
        if article_date is not None:
            published_date = datetime(
                article_date.year,
                article_date.month,
                article_date.day,
                tzinfo=ZoneInfo(tz),
            )

        if only_date is not None:
            if article_date is None or article_date != only_date:
                continue

        articles.append(
            DiscoveredArticle(
                article_id=article_id,
                source_url=url,
                published_date=published_date,
            )
        )

    # IDs have two layouts (`neYYYY...` and `YYYYMMDDde...`); lexical ordering
    # would put every legacy `ne` ID ahead of newer current-format IDs.
    # Sort by the date derived above so `--latest` is chronological regardless
    # of which ID layout the sitemap uses.
    fallback_date = datetime.min.replace(tzinfo=ZoneInfo(tz))
    articles.sort(
        key=lambda item: (
            item.published_date is not None,
            item.published_date or fallback_date,
            item.article_id,
        ),
        reverse=True,
    )
    return articles
