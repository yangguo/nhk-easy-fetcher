"""Discover article URLs from the NHK EASY sitemap."""

from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from nhk_easy_fetcher.models import DiscoveredArticle

ARTICLE_RE = re.compile(r"/easy/(ne\d{13})/\1\.html$")
ARTICLE_ID_DATE_RE = re.compile(r"^ne(\d{4})(\d{2})(\d{2})")


def article_id_to_date(article_id: str, tz: str = "Asia/Tokyo") -> date | None:
    match = ARTICLE_ID_DATE_RE.match(article_id)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


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
        match = ARTICLE_RE.search(url)
        if not match:
            continue
        article_id = match.group(1)
        if article_id in seen:
            continue
        seen.add(article_id)

        published_date: datetime | None = None
        article_date = article_id_to_date(article_id, tz)
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

    articles.sort(key=lambda item: item.article_id, reverse=True)
    return articles
