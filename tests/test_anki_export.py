"""Tests for Anki deck export."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from nhk_easy_fetcher.anki_export import discover_article_dirs, export_apkg, load_article
from nhk_easy_fetcher.cli import app
from nhk_easy_fetcher.errors import AnkiExportError
from nhk_easy_fetcher.models import (
    ArticleRecord,
    AudioInfo,
    ContentStatus,
    PageMode,
    Paragraph,
    TextView,
)
from nhk_easy_fetcher.parser import parse_complete_article
from nhk_easy_fetcher.storage import StateStore

runner = CliRunner()


def _sample_article(
    *,
    article_id: str = "ne2026090512345",
    status: ContentStatus = ContentStatus.COMPLETE,
    paragraphs: list[Paragraph] | None = None,
) -> ArticleRecord:
    return ArticleRecord(
        article_id=article_id,
        source_url=f"https://news.web.nhk/news/easy/{article_id}/{article_id}.html",
        fetched_at=datetime(2026, 9, 5, 1, 0, tzinfo=ZoneInfo("UTC")),
        published_at=datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        content_status=status,
        parser_mode=PageMode.CLASSIC_COMPLETE,
        title=TextView(
            plain="漢字のニュース",
            with_readings="漢字（かんじ）のニュース",
            html_with_ruby="<ruby>漢字<rt>かんじ</rt></ruby>のニュース",
        ),
        paragraphs=paragraphs
        or [
            Paragraph(
                plain="漢字を読む。",
                with_readings="漢字（かんじ）を読む。",
                html_with_ruby="<ruby>漢字<rt>かんじ</rt></ruby>を読む。",
            ),
            Paragraph(
                plain="二つ目の段落です。",
                with_readings="二つ目の段落です。",
                html_with_ruby="二つ目の段落です。",
            ),
        ],
    )


def _write_article(tmp_path: Path, article: ArticleRecord) -> Path:
    store = StateStore(tmp_path)
    return store.save_complete(article, {"json"})


def test_export_apkg_creates_valid_deck(tmp_path: Path) -> None:
    article_dir = _write_article(tmp_path, _sample_article())
    deck_path = tmp_path / "study.apkg"

    summary = export_apkg([article_dir], deck_name="NHK EASY Test", output_path=deck_path)

    assert summary.card_count == 2
    assert summary.article_count == 1
    assert deck_path.is_file()
    with zipfile.ZipFile(deck_path) as archive:
        assert "collection.anki2" in archive.namelist()


def test_export_front_uses_readings_by_default(tmp_path: Path) -> None:
    article_dir = _write_article(tmp_path, _sample_article())
    deck_path = tmp_path / "readings.apkg"
    export_apkg([article_dir], deck_name="Readings", output_path=deck_path, furigana="readings")

    with zipfile.ZipFile(deck_path) as archive:
        collection = archive.read("collection.anki2").decode("utf-8", errors="ignore")
    assert "漢字（かんじ）を読む。" in collection
    assert "personal study only" in collection


def test_export_plain_furigana_mode(tmp_path: Path) -> None:
    article_dir = _write_article(tmp_path, _sample_article())
    deck_path = tmp_path / "plain.apkg"
    export_apkg([article_dir], deck_name="Plain", output_path=deck_path, furigana="plain")

    with zipfile.ZipFile(deck_path) as archive:
        collection = archive.read("collection.anki2").decode("utf-8", errors="ignore")
    assert "漢字を読む。" in collection
    assert "漢字（かんじ）を読む。" not in collection


def test_export_include_audio_attaches_existing_file(tmp_path: Path) -> None:
    article = _sample_article()
    article = article.model_copy(
        update={"audio": AudioInfo(status="available", format="m4a", sha256="abc")}
    )
    article_dir = _write_article(tmp_path, article)
    audio_path = article_dir / "audio.m4a"
    audio_path.write_bytes(b"fake-audio")

    deck_path = tmp_path / "audio.apkg"
    export_apkg(
        [article_dir],
        deck_name="Audio",
        output_path=deck_path,
        include_audio=True,
    )

    with zipfile.ZipFile(deck_path) as archive:
        collection = archive.read("collection.anki2").decode("utf-8", errors="ignore")
        assert "[sound:audio.m4a]" in collection
        assert "media" in archive.namelist()


def test_load_article_missing_json_raises(tmp_path: Path) -> None:
    with pytest.raises(AnkiExportError, match="No article.json"):
        load_article(tmp_path)


def test_export_incomplete_article_raises(tmp_path: Path) -> None:
    article_dir = _write_article(
        tmp_path,
        _sample_article(status=ContentStatus.PARTIAL),
    )
    with pytest.raises(AnkiExportError, match="not complete"):
        export_apkg([article_dir], deck_name="Partial", output_path=tmp_path / "x.apkg")


def test_discover_article_dirs_skips_incomplete(tmp_path: Path) -> None:
    complete_dir = _write_article(tmp_path, _sample_article(article_id="ne-complete"))
    _write_article(
        tmp_path,
        _sample_article(article_id="ne-partial", status=ContentStatus.PARTIAL),
    )

    discovered = discover_article_dirs(StateStore(tmp_path).articles_root)
    assert discovered == [complete_dir]


def test_discover_article_dirs_date_filter(tmp_path: Path) -> None:
    early = _sample_article(article_id="ne-early")
    early = early.model_copy(
        update={"published_at": datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))}
    )
    late = _sample_article(article_id="ne-late")
    late = late.model_copy(
        update={"published_at": datetime(2026, 9, 10, 10, 0, tzinfo=ZoneInfo("Asia/Tokyo"))}
    )
    _write_article(tmp_path, early)
    late_dir = _write_article(tmp_path, late)

    discovered = discover_article_dirs(
        StateStore(tmp_path).articles_root,
        since=datetime(2026, 9, 5).date(),
    )
    assert discovered == [late_dir]


def test_export_from_parsed_fixture(tmp_path: Path, load_fixture) -> None:
    article = parse_complete_article(
        load_fixture("classic_complete.html"),
        source_url="https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html",
    )
    article_dir = _write_article(tmp_path, article)
    deck_path = tmp_path / "fixture.apkg"

    summary = export_apkg([article_dir], deck_name="Fixture", output_path=deck_path)
    assert summary.card_count == len(article.paragraphs)


def test_cli_export_anki_happy_path(tmp_path: Path) -> None:
    _write_article(tmp_path, _sample_article())
    deck_path = tmp_path / ".nhk-easy-fetcher" / "anki" / "nhk-easy.apkg"

    result = runner.invoke(
        app,
        ["export-anki", "--output", str(tmp_path), "--deck", "nhk-easy"],
    )
    assert result.exit_code == 0
    assert deck_path.is_file()
    assert "personal study only" in result.stdout + result.stderr


def test_cli_export_single_article_dir(tmp_path: Path) -> None:
    article_dir = _write_article(tmp_path, _sample_article())
    deck_path = tmp_path / "one.apkg"

    result = runner.invoke(
        app,
        ["export-anki", str(article_dir), "--output", str(deck_path)],
    )
    assert result.exit_code == 0
    assert deck_path.is_file()


def test_cli_export_missing_article_dir(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["export-anki", str(tmp_path / "missing"), "--output", str(tmp_path)],
    )
    assert result.exit_code == 2
    assert "not found" in (result.stdout + result.stderr)


def test_cli_export_incomplete_article(tmp_path: Path) -> None:
    article_dir = _write_article(
        tmp_path,
        _sample_article(status=ContentStatus.PARTIAL),
    )
    result = runner.invoke(app, ["export-anki", str(article_dir)])
    assert result.exit_code == 2
    assert "not complete" in (result.stdout + result.stderr)


def test_cli_anki_alias(tmp_path: Path) -> None:
    _write_article(tmp_path, _sample_article())
    result = runner.invoke(app, ["anki", "--output", str(tmp_path)])
    assert result.exit_code == 0
