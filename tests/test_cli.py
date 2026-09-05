from pathlib import Path

import respx
from typer.testing import CliRunner

from nhk_easy_fetcher.audio import resolve_hls_url
from nhk_easy_fetcher.cli import app
from nhk_easy_fetcher.models import FetchResult, RunSummary
from nhk_easy_fetcher.parser import parse_complete_article

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


def test_authorization_failure_exit_code() -> None:
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


def test_fetch_partial_on_next_shell(tmp_path: Path) -> None:
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


def test_audio_flag_sets_config_mode(tmp_path: Path) -> None:
    sitemap = (Path(__file__).parent / "fixtures" / "easy_sitemap.xml").read_text()
    partial = (Path(__file__).parent / "fixtures" / "next_partial.html").read_text()
    with respx.mock:
        respx.get("https://news.web.nhk/news/easy/sitemap/sitemap.xml").respond(200, text=sitemap)
        respx.get("https://news.web.nhk/news/easy/top-list.json").respond(401)
        respx.get("https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html").respond(
            200, text=partial
        )
        result = runner.invoke(
            app,
            ["fetch", "--latest", "--audio", "m4a", "--output", str(tmp_path), "--dry-run"],
        )
    assert result.exit_code == 0


def test_invalid_audio_mode_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["fetch-latest", "--audio", "wav", "--output", str(tmp_path)],
    )
    assert result.exit_code == 2


def test_hls_url_uses_media_vd_st_nhk() -> None:
    url = resolve_hls_url("voice-20260905.mp4")
    assert url.startswith("https://media.vd.st.nhk/news/easy_audio/")
    assert "vod-stream.nhk.jp" not in url


def test_parser_accepts_current_article_id_format(load_fixture) -> None:
    article = parse_complete_article(
        load_fixture("classic_complete.html"),
        source_url="https://news.web.nhk/news/easy/20260904de48127/20260904de48127.html",
    )
    assert article.article_id == "20260904de48127"
