"""Supplemental metadata from top-list.json and article HTML."""

from __future__ import annotations

import json
import re
from typing import Any

from nhk_easy_fetcher.errors import AuthorizationUnavailable

TOP_LIST_URL = "https://news.web.nhk/news/easy/top-list.json"
VOICE_URI_HTML_RE = re.compile(
    r'"news_easy_voice_uri"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)


def extract_voice_uri_from_html(html: str) -> str | None:
    match = VOICE_URI_HTML_RE.search(html)
    return match.group(1) if match else None


def _item_article_id(item: dict[str, Any]) -> str | None:
    for key in ("news_id", "article_id", "id", "easy_id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def parse_top_list_voice_map(payload: str | dict[str, Any]) -> dict[str, str]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    items: list[dict[str, Any]] = []
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        for key in ("list", "items", "news", "articles"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                items = [item for item in candidate if isinstance(item, dict)]
                break

    mapping: dict[str, str] = {}
    for item in items:
        article_id = _item_article_id(item)
        voice_uri = item.get("news_easy_voice_uri")
        if article_id and isinstance(voice_uri, str) and voice_uri:
            mapping[article_id] = voice_uri
    return mapping


def fetch_top_list_voice_map(
    client: Any,
    *,
    url: str = TOP_LIST_URL,
) -> dict[str, str]:
    """Fetch top-list.json; requires an authorized HTTP client session."""
    try:
        text = client.get_text(url)
    except AuthorizationUnavailable:
        raise
    except Exception as exc:
        raise AuthorizationUnavailable(f"top-list.json unavailable: {exc}") from exc
    return parse_top_list_voice_map(text)


def resolve_voice_uri(
    *,
    article_id: str,
    html: str,
    top_list_map: dict[str, str] | None = None,
) -> str | None:
    from_html = extract_voice_uri_from_html(html)
    if from_html:
        return from_html
    if top_list_map is not None:
        return top_list_map.get(article_id)
    return None
