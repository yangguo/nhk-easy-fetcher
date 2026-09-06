"""Redacted summaries for live source-contract checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractSummary:
    """Structural probe result without article bodies or secrets."""

    status_code: int
    page_mode: str
    body_hash: str
    content_type: str | None = None
    has_article_title: bool = False
    has_article_body: bool = False
    article_id: str | None = None

    @staticmethod
    def body_hash_prefix(body: str, *, length: int = 12) -> str:
        digest = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        return digest[:length]

    def to_dict(self) -> dict[str, object]:
        return {
            "status_code": self.status_code,
            "page_mode": self.page_mode,
            "body_hash_prefix": self.body_hash,
            "content_type": self.content_type,
            "has_article_title": self.has_article_title,
            "has_article_body": self.has_article_body,
            "article_id": self.article_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
