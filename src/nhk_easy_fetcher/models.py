"""Domain models for article records and fetch state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from nhk_easy_fetcher import __version__


class PageMode(StrEnum):
    CLASSIC_COMPLETE = "classic_complete"
    NEXT_PARTIAL = "next_partial"
    UNKNOWN = "unknown"


class ContentStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"

    @classmethod
    def for_page_mode(cls, page_mode: PageMode) -> ContentStatus:
        return cls.COMPLETE if page_mode is PageMode.CLASSIC_COMPLETE else cls.UNAVAILABLE


class TextView(BaseModel):
    plain: str
    with_readings: str
    html_with_ruby: str


class Paragraph(BaseModel):
    plain: str
    with_readings: str
    html_with_ruby: str


class AudioInfo(BaseModel):
    status: Literal["available", "unavailable", "not_requested"] = "not_requested"
    format: str | None = None
    sha256: str | None = None
    manifest_url: str | None = None


class Provenance(BaseModel):
    tool_version: str = __version__
    source_contract_version: int = 1


class ArticleRecord(BaseModel):
    schema_version: int = 1
    article_id: str
    source_url: str
    fetched_at: datetime
    published_at: datetime | None = None
    content_status: ContentStatus
    parser_mode: PageMode
    title: TextView
    paragraphs: list[Paragraph]
    image_url: str | None = None
    audio: AudioInfo = Field(default_factory=AudioInfo)
    provenance: Provenance = Field(default_factory=Provenance)


class DiscoveredArticle(BaseModel):
    article_id: str
    source_url: str
    published_date: datetime | None = None


class FetchResult(BaseModel):
    article_id: str
    source_url: str
    status: Literal[
        "completed",
        "skipped",
        "authorization_required",
        "source_contract_changed",
        "partial",
        "failed",
    ]
    message: str = ""
    output_dir: str | None = None


class RunSummary(BaseModel):
    completed: int = 0
    skipped: int = 0
    authorization_required: int = 0
    source_contract_changed: int = 0
    partial: int = 0
    failed: int = 0
    results: list[FetchResult] = Field(default_factory=list)

    def add(self, result: FetchResult) -> None:
        self.results.append(result)
        if result.status == "completed":
            self.completed += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "authorization_required":
            self.authorization_required += 1
        elif result.status == "source_contract_changed":
            self.source_contract_changed += 1
        elif result.status == "partial":
            self.partial += 1
        else:
            self.failed += 1

    @property
    def exit_code(self) -> int:
        if self.authorization_required > 0 and self.completed == 0:
            return 3
        if self.source_contract_changed > 0 and self.completed == 0:
            return 4
        if self.failed > 0 and self.completed == 0:
            return 1
        if self.partial > 0 or (self.failed > 0 and self.completed > 0):
            return 5
        return 0
