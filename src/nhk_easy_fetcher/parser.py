"""Parse complete classic EASY article HTML."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from nhk_easy_fetcher.article_ids import extract_article_id
from nhk_easy_fetcher.errors import FullContentUnavailable, ParseSuspect
from nhk_easy_fetcher.models import (
    ArticleRecord,
    ContentStatus,
    PageMode,
    Paragraph,
    TextView,
)
from nhk_easy_fetcher.page_mode import detect_page_mode

DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def _required(node: Node | None, name: str) -> Node:
    if node is None:
        raise ParseSuspect(f"Missing required element: {name}")
    return node


def _strip_event_attrs(node: Node) -> None:
    for attr in list(node.attributes.keys()):
        if attr.startswith("on"):
            del node.attributes[attr]


def _strip_ruby_to_plain(html: str) -> str:
    without_rt = re.sub(r"<rt[^>]*>.*?</rt>", "", html, flags=re.DOTALL | re.IGNORECASE)
    without_rp = re.sub(r"<rp[^>]*>.*?</rp>", "", without_rt, flags=re.DOTALL | re.IGNORECASE)
    without_tags = re.sub(r"</?ruby[^>]*>", "", without_rp, flags=re.IGNORECASE)
    return HTMLParser(without_tags).text(strip=True)


def _ruby_to_readings(html: str) -> str:
    with_readings = re.sub(
        r"<rt[^>]*>(.*?)</rt>",
        r"（\1）",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    without_rp = re.sub(r"<rp[^>]*>.*?</rp>", "", with_readings, flags=re.DOTALL | re.IGNORECASE)
    without_tags = re.sub(r"</?ruby[^>]*>", "", without_rp, flags=re.IGNORECASE)
    return HTMLParser(without_tags).text(strip=True)


def _node_to_plain(node: Node) -> str:
    return _strip_ruby_to_plain(node.html or "")


def _node_to_readings(node: Node) -> str:
    return _ruby_to_readings(node.html or "")


def _node_to_html(node: Node) -> str:
    fragment = HTMLParser(node.html or "").body
    if fragment is None:
        return node.html or ""
    for tag in fragment.css("script, style"):
        tag.decompose()
    for anchor in fragment.css("a"):
        anchor.attributes.pop("href", None)
    for elem in fragment.css("*"):
        _strip_event_attrs(elem)
    return fragment.html or ""


def _parse_text_view(node: Node) -> TextView:
    return TextView(
        plain=_node_to_plain(node),
        with_readings=_node_to_readings(node),
        html_with_ruby=_node_to_html(node).strip(),
    )


def _parse_date(tree: HTMLParser, tz: str = "Asia/Tokyo") -> datetime | None:
    date_node = tree.css_first(".article-date")
    if date_node is None:
        return None
    text = date_node.text(strip=True)
    match = DATE_RE.search(text)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return datetime(year, month, day, tzinfo=ZoneInfo(tz))
    except ValueError:
        return None


def parse_complete_article(
    html: str,
    *,
    source_url: str,
    fetched_at: datetime | None = None,
    tz: str = "Asia/Tokyo",
) -> ArticleRecord:
    page_mode = detect_page_mode(html)
    if page_mode is not PageMode.CLASSIC_COMPLETE:
        raise FullContentUnavailable(source_url, page_mode=page_mode.value)

    tree = HTMLParser(html)
    title_node = _required(tree.css_first(".article-title"), "title")
    body = _required(tree.css_first("#js-article-body"), "article body")

    paragraphs: list[Paragraph] = []
    for p_node in body.css("p"):
        if not p_node.text(strip=True):
            continue
        view = _parse_text_view(p_node)
        paragraphs.append(
            Paragraph(
                plain=view.plain,
                with_readings=view.with_readings,
                html_with_ruby=view.html_with_ruby,
            )
        )

    title = _parse_text_view(title_node)
    if not title.plain or not paragraphs:
        raise ParseSuspect("Title or body paragraphs are empty")

    article_id = extract_article_id(source_url)
    if article_id is None:
        raise ParseSuspect(f"Could not extract article ID from {source_url}")

    og_image = tree.css_first('meta[property="og:image"]')
    image_url = og_image.attributes.get("content") if og_image else None

    now = fetched_at or datetime.now(tz=ZoneInfo("UTC"))
    return ArticleRecord(
        article_id=article_id,
        source_url=source_url,
        fetched_at=now,
        published_at=_parse_date(tree, tz),
        content_status=ContentStatus.COMPLETE,
        parser_mode=PageMode.CLASSIC_COMPLETE,
        title=title,
        paragraphs=paragraphs,
        image_url=image_url,
    )
