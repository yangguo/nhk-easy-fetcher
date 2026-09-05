"""Local file storage, SQLite state, and article exports."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from nhk_easy_fetcher.audio import AudioDownloadResult
from nhk_easy_fetcher.errors import LocalWriteFailed
from nhk_easy_fetcher.models import ArticleRecord, ContentStatus

EXPORT_FORMATS = frozenset({"json", "markdown", "text", "html"})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    mode = "wb" if isinstance(content, bytes) else "w"
    try:
        with tmp.open(mode, encoding=None if mode == "wb" else "utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except OSError as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise LocalWriteFailed(str(exc)) from exc


class StateStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.expanduser().resolve()
        self.meta_dir = self.output_dir / ".nhk-easy-fetcher"
        self.articles_root = self.output_dir / "articles"
        self.db_path = self.meta_dir / "state.sqlite3"
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.articles_root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    article_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    content_status TEXT NOT NULL,
                    parser_mode TEXT,
                    completed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    article_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (article_id, kind)
                )
                """
            )
            conn.commit()

    def article_dir(self, article_id: str, published_at: datetime | None = None) -> Path:
        if published_at is not None:
            date_part = published_at.strftime("%Y-%m-%d")
            month_part = published_at.strftime("%Y-%m")
            year_part = published_at.strftime("%Y")
            return self.articles_root / year_part / month_part / f"{date_part}_{article_id}"
        return self.articles_root / "unknown" / article_id

    def should_fetch(self, article_id: str, formats: Iterable[str]) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT content_status FROM articles WHERE article_id = ?",
                (article_id,),
            ).fetchone()
            if row is None or row[0] != ContentStatus.COMPLETE.value:
                return True

            for fmt in formats:
                artifact = conn.execute(
                    "SELECT path, sha256 FROM artifacts WHERE article_id = ? AND kind = ?",
                    (article_id, fmt),
                ).fetchone()
                if artifact is None:
                    return True
                path = Path(artifact[0])
                if not path.exists():
                    return True
                if _sha256_file(path) != artifact[1]:
                    return True
        return False

    def get_status(self, article_id: str) -> str | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT content_status FROM articles WHERE article_id = ?",
                (article_id,),
            ).fetchone()
            return row[0] if row else None

    def save_complete(
        self,
        article: ArticleRecord,
        formats: set[str],
        *,
        audio_result: AudioDownloadResult | None = None,
    ) -> Path:
        out_dir = self.article_dir(article.article_id, article.published_at)
        out_dir.mkdir(parents=True, exist_ok=True)

        written: dict[str, Path] = {}
        if "json" in formats:
            json_path = out_dir / "article.json"
            _atomic_write(json_path, article.model_dump_json(indent=2))
            written["json"] = json_path
        if "markdown" in formats:
            md_path = out_dir / "article.md"
            _atomic_write(md_path, render_markdown(article))
            written["markdown"] = md_path
        if "text" in formats:
            txt_path = out_dir / "article.txt"
            _atomic_write(txt_path, render_text(article))
            written["text"] = txt_path
        if "html" in formats:
            html_path = out_dir / "article.html"
            _atomic_write(html_path, render_html(article))
            written["html"] = html_path

        if audio_result is not None and audio_result.output_path.exists():
            kind = f"audio_{audio_result.format}"
            written[kind] = audio_result.output_path

        checksum_lines: list[str] = []
        for kind, path in written.items():
            checksum_lines.append(f"{_sha256_file(path)}  {path.name}")

        checksum_path = out_dir / "checksums.sha256"
        _atomic_write(checksum_path, "\n".join(checksum_lines) + "\n")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO articles
                    (article_id, source_url, content_status, parser_mode, completed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        article.article_id,
                        article.source_url,
                        article.content_status.value,
                        article.parser_mode.value,
                        article.fetched_at.isoformat(),
                    ),
                )
                for kind, path in written.items():
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO artifacts
                        (article_id, kind, path, sha256)
                        VALUES (?, ?, ?, ?)
                        """,
                        (article.article_id, kind, str(path), _sha256_file(path)),
                    )
                conn.commit()
            except sqlite3.Error as exc:
                conn.rollback()
                raise LocalWriteFailed(str(exc)) from exc

        return out_dir


def render_markdown(article: ArticleRecord) -> str:
    published = article.published_at.isoformat() if article.published_at else "unknown"
    lines = [
        f"# {article.title.plain}",
        "",
        f"> **Source:** [{article.source_url}]({article.source_url})",
        f"> **Published:** {published}",
        f"> **Fetched:** {article.fetched_at.isoformat()}",
        f"> **Status:** {article.content_status.value}",
        "",
        "> NHK content — personal study only, do not redistribute.",
        "",
    ]
    for paragraph in article.paragraphs:
        lines.append(paragraph.plain)
        lines.append("")
    return "\n".join(lines)


def render_text(article: ArticleRecord) -> str:
    lines = [article.title.plain, ""]
    for paragraph in article.paragraphs:
        lines.append(paragraph.plain)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_html(article: ArticleRecord) -> str:
    body_parts = [f"<h1>{article.title.html_with_ruby}</h1>"]
    for paragraph in article.paragraphs:
        body_parts.append(f"<p>{paragraph.html_with_ruby}</p>")
    return (
        '<!DOCTYPE html><html lang="ja"><head>'
        f'<meta charset="utf-8"><title>{article.title.plain}</title>'
        "</head><body>" + "\n".join(body_parts) + "</body></html>"
    )
