"""Article ID extraction and date parsing for legacy and current EASY IDs."""

from __future__ import annotations

import re
from datetime import date

# Legacy: ne2026090512345  |  Current: 20260904de48127
ARTICLE_ID_PATTERN = r"(?:ne\d{13}|\d{8}de\d+)"
ARTICLE_URL_RE = re.compile(rf"/easy/(?P<id>{ARTICLE_ID_PATTERN})/(?P=id)\.html$")
LEGACY_DATE_RE = re.compile(r"^ne(\d{4})(\d{2})(\d{2})")
CURRENT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})de\d+$")


def extract_article_id(source_url: str) -> str | None:
    match = ARTICLE_URL_RE.search(source_url)
    return match.group("id") if match else None


def article_id_to_date(article_id: str) -> date | None:
    match = LEGACY_DATE_RE.match(article_id) or CURRENT_DATE_RE.match(article_id)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None
