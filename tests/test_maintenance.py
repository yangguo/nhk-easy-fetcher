from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from nhk_easy_fetcher.cli import app
from nhk_easy_fetcher.maintenance import (
    cleanup_partials,
    parse_checksums_file,
    verify_article_dir,
    verify_output,
)
from nhk_easy_fetcher.models import (
    ArticleRecord,
    ContentStatus,
    PageMode,
    Paragraph,
    TextView,
)
from nhk_easy_fetcher.storage import StateStore

runner = CliRunner()


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


def test_verify_passes_for_saved_article(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    article = _sample_article()
    article_dir = store.save_complete(article, {"json", "markdown", "text"})
    report = verify_article_dir(article_dir)
    assert report.ok
    assert report.issues == []


def test_verify_detects_hash_mismatch(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    article = _sample_article()
    article_dir = store.save_complete(article, {"json", "markdown", "text"})
    (article_dir / "article.txt").write_text("corrupted", encoding="utf-8")
    report = verify_article_dir(article_dir)
    assert not report.ok
    assert any("hash mismatch" in issue.message for issue in report.issues)


def test_parse_checksums_file(tmp_path: Path) -> None:
    checksums = tmp_path / "checksums.sha256"
    checksums.write_text("deadbeef  article.json\n", encoding="utf-8")
    assert parse_checksums_file(checksums) == {"article.json": "deadbeef"}


def test_cleanup_removes_partial_files(tmp_path: Path) -> None:
    partial = tmp_path / "articles" / "2026" / "foo.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"tmp")
    actions = cleanup_partials(tmp_path, dry_run=False)
    assert len(actions) == 1
    assert actions[0].removed
    assert not partial.exists()


def test_cleanup_dry_run_keeps_files(tmp_path: Path) -> None:
    partial = tmp_path / "articles" / "stale.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"tmp")
    actions = cleanup_partials(tmp_path, dry_run=True)
    assert len(actions) == 1
    assert not actions[0].removed
    assert partial.exists()


def test_verify_cli_success(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    article_dir = store.save_complete(_sample_article(), {"json", "text"})
    result = runner.invoke(app, ["verify", str(article_dir)])
    assert result.exit_code == 0
    assert "ok" in (result.stdout + result.stderr).lower()


def test_verify_cli_failure_exit_code(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    article_dir = store.save_complete(_sample_article(), {"json", "text"})
    (article_dir / "article.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["verify", str(article_dir)])
    assert result.exit_code == 1


def test_cleanup_cli_dry_run(tmp_path: Path) -> None:
    partial = tmp_path / "articles" / "x.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"x")
    result = runner.invoke(app, ["cleanup", "--output", str(tmp_path), "--dry-run", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout + result.stderr)
    assert payload[0]["dry_run"] is True
    assert partial.exists()


def test_verify_output_tree(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    store.save_complete(_sample_article(), {"json"})
    reports = verify_output(tmp_path)
    assert len(reports) == 1
    assert reports[0].ok


def test_verify_audio_uses_ffprobe(tmp_path: Path, monkeypatch) -> None:
    store = StateStore(tmp_path)
    article_dir = store.save_complete(_sample_article(), {"json"})
    audio_path = article_dir / "audio.m4a"
    audio_path.write_bytes(b"not-real-audio")

    mock_runner = MagicMock()
    mock_runner.run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(
            {"streams": [{"codec_type": "audio"}], "format": {"duration": "12.5"}}
        ),
        stderr="",
    )
    monkeypatch.setattr(
        "nhk_easy_fetcher.maintenance.SubprocessRunner",
        lambda: mock_runner,
    )

    report = verify_article_dir(article_dir, check_audio=True)
    assert report.ok
    assert mock_runner.run.called
