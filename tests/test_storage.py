import gc
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

from nhk_easy_fetcher.models import (
    ArticleRecord,
    ContentStatus,
    PageMode,
    Paragraph,
    TextView,
)
from nhk_easy_fetcher.storage import StateStore, render_markdown, render_text


def _sample_article() -> ArticleRecord:
    return ArticleRecord(
        article_id="ne2026090512345",
        source_url="https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html",
        fetched_at=datetime(2026, 9, 5, 1, 0, tzinfo=ZoneInfo("UTC")),
        published_at=datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        content_status=ContentStatus.COMPLETE,
        parser_mode=PageMode.CLASSIC_COMPLETE,
        title=TextView(plain="タイトル", with_readings="タイトル", html_with_ruby="タイトル"),
        paragraphs=[
            Paragraph(plain="本文", with_readings="本文", html_with_ruby="本文"),
        ],
    )


def test_complete_article_is_skipped_only_when_requested_artifacts_verify(tmp_path) -> None:
    store = StateStore(tmp_path)
    article = _sample_article()
    store.save_complete(article, {"json", "markdown", "text"})
    assert store.should_fetch(article.article_id, {"json", "markdown", "text"}) is False
    (store.article_dir(article.article_id, article.published_at) / "article.md").unlink()
    assert store.should_fetch(article.article_id, {"json", "markdown", "text"}) is True


def test_exports_include_warning(tmp_path) -> None:
    article = _sample_article()
    md = render_markdown(article)
    assert "personal study only" in md
    txt = render_text(article)
    assert "タイトル" in txt


def test_refetch_does_not_keep_old_unrequested_artifact_verified(tmp_path) -> None:
    store = StateStore(tmp_path)
    old_article = _sample_article()
    store.save_complete(old_article, {"json", "markdown"})

    new_article = old_article.model_copy(
        update={
            "title": TextView(
                plain="新しいタイトル",
                with_readings="新しいタイトル",
                html_with_ruby="新しいタイトル",
            )
        }
    )
    store.save_complete(new_article, {"json"})

    assert store.should_fetch(new_article.article_id, {"markdown"}) is True


def test_state_store_closes_sqlite_connections(tmp_path) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        store = StateStore(tmp_path)
        store.should_fetch("ne2026090512345", {"json"})
        del store
        gc.collect()

    assert not [warning for warning in caught if "unclosed database" in str(warning.message)]
