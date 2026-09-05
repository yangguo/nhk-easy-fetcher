from pathlib import Path

import respx
from typer.testing import CliRunner

from nhk_easy_fetcher.cli import app
from nhk_easy_fetcher.models import FetchResult, RunSummary

runner = CliRunner()


def test_dry_run_does_not_create_output(tmp_path: Path) -> None:
    sitemap = (Path(__file__).parent / "fixtures" / "easy_sitemap.xml").read_text()
    with respx.mock:
        respx.get("https://news.web.nhk/news/easy/sitemap/sitemap.xml").respond(200, text=sitemap)
        result = runner.invoke(
            app,
            ["fetch", "--latest", "--output", str(tmp_path), "--dry-run"],
        )
    assert result.exit_code == 0
    assert not (tmp_path / ".nhk-easy-fetcher").exists()


def test_probe_offline() -> None:
    result = runner.invoke(app, ["probe"])
    assert result.exit_code == 0
    assert "classic_complete" in (result.stdout + result.stderr)


def test_live_probe_requires_env() -> None:
    result = runner.invoke(app, ["probe", "--live", "--i-understand-live-requests"])
    assert result.exit_code == 2


def test_authorization_failure_exit_code(monkeypatch, tmp_path: Path) -> None:
    summary = RunSummary()
    summary.add(
        FetchResult(
            article_id="ne2026090512345",
            source_url="https://example.test",
            status="authorization_required",
            message="auth needed",
        )
    )
    assert summary.exit_code == 3

    sitemap = (Path(__file__).parent / "fixtures" / "easy_sitemap.xml").read_text()
    partial = (Path(__file__).parent / "fixtures" / "next_partial.html").read_text()
    with respx.mock:
        respx.get("https://news.web.nhk/news/easy/sitemap/sitemap.xml").respond(200, text=sitemap)
        respx.get("https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html").respond(
            200, text=partial
        )
        result = runner.invoke(
            app,
            ["fetch-latest", "--output", str(tmp_path)],
        )
    assert result.exit_code == 3
