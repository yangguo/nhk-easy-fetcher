from pathlib import Path

import respx
from typer.testing import CliRunner

from nhk_easy_fetcher.app import FetchApplication
from nhk_easy_fetcher.auth import FixtureAuthProvider
from nhk_easy_fetcher.config import AppConfig
from nhk_easy_fetcher.storage import StateStore

runner = CliRunner()


def test_run_skips_known_complete_article_and_fetches_only_new_article(tmp_path: Path) -> None:
    sitemap = (Path(__file__).parent / "fixtures" / "easy_sitemap.xml").read_text()
    classic = (Path(__file__).parent / "fixtures" / "classic_complete.html").read_text()

    config = AppConfig()
    config.storage.output_dir = tmp_path
    config.fetch.min_interval_seconds = 0.01
    store = StateStore(tmp_path)
    app = FetchApplication(config, store=store)

    with respx.mock:
        respx.get(config.discovery.sitemap_url).respond(200, text=sitemap)
        respx.get("https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html").respond(
            200, text=classic
        )

        first = app.fetch_latest()
        second = app.fetch_latest()

    assert first.completed == 1
    assert second.skipped == 1


def test_audio_authorization_failure_still_exports_text_as_partial(tmp_path: Path) -> None:
    sitemap = (Path(__file__).parent / "fixtures" / "easy_sitemap.xml").read_text()
    classic = (Path(__file__).parent / "fixtures" / "classic_complete.html").read_text()

    config = AppConfig()
    config.storage.output_dir = tmp_path
    config.fetch.min_interval_seconds = 0.01
    config.audio.mode = "m4a"
    app = FetchApplication(
        config,
        auth_provider=FixtureAuthProvider({"z_at": "expired"}),
    )

    with respx.mock:
        respx.get(config.discovery.sitemap_url).respond(200, text=sitemap)
        respx.get("https://news.web.nhk/news/easy/top-list.json").respond(
            200,
            json={
                "items": [
                    {
                        "news_id": "ne2026090512345",
                        "news_easy_voice_uri": "voice-20260905.mp4",
                    }
                ]
            },
        )
        respx.get(
            "https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html"
        ).respond(200, text=classic)
        token = respx.post("https://mediatoken.web.nhk/v1/token").respond(401)

        summary = app.fetch_latest()

    assert summary.partial == 1
    assert summary.failed == 0
    assert token.call_count == 1
    assert list(tmp_path.rglob("article.json"))


def test_missing_audio_metadata_still_exports_text_as_partial(tmp_path: Path) -> None:
    sitemap = (Path(__file__).parent / "fixtures" / "easy_sitemap.xml").read_text()
    classic = (Path(__file__).parent / "fixtures" / "classic_complete.html").read_text()

    config = AppConfig()
    config.storage.output_dir = tmp_path
    config.fetch.min_interval_seconds = 0.01
    config.audio.mode = "m4a"
    app = FetchApplication(config)

    with respx.mock:
        respx.get(config.discovery.sitemap_url).respond(200, text=sitemap)
        respx.get(config.discovery.top_list_url).respond(200, json={"items": []})
        respx.get(
            "https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html"
        ).respond(200, text=classic)

        summary = app.fetch_latest()

    assert summary.partial == 1
    assert summary.completed == 0
    assert list(tmp_path.rglob("article.json"))


def test_malformed_audio_metadata_still_exports_text_as_partial(tmp_path: Path) -> None:
    sitemap = (Path(__file__).parent / "fixtures" / "easy_sitemap.xml").read_text()
    classic = (Path(__file__).parent / "fixtures" / "classic_complete.html").read_text()

    config = AppConfig()
    config.storage.output_dir = tmp_path
    config.fetch.min_interval_seconds = 0.01
    config.audio.mode = "m4a"
    app = FetchApplication(config)

    with respx.mock:
        respx.get(config.discovery.sitemap_url).respond(200, text=sitemap)
        respx.get(config.discovery.top_list_url).respond(
            200,
            json={
                "items": [
                    {
                        "news_id": "ne2026090512345",
                        "news_easy_voice_uri": ".",
                    }
                ]
            },
        )
        respx.get(
            "https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html"
        ).respond(200, text=classic)

        summary = app.fetch_latest()

    assert summary.partial == 1
    assert summary.completed == 0
    assert list(tmp_path.rglob("article.json"))


def test_unknown_page_mode_is_reported_as_source_contract_change(tmp_path: Path) -> None:
    config = AppConfig()
    config.storage.output_dir = tmp_path
    app = FetchApplication(config, store=StateStore(tmp_path))

    class FakeClient:
        def get_text(self, url: str) -> str:
            return "<html><h1>maintenance</h1></html>"

    result = app._fetch_one(
        FakeClient(),
        "https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html",
        "ne2026090512345",
        {"json"},
    )

    assert result.status == "source_contract_changed"
    assert result.message


def test_unknown_page_mode_is_contract_change_even_with_allow_partial(tmp_path: Path) -> None:
    config = AppConfig()
    config.storage.output_dir = tmp_path
    config.fetch.allow_partial = True
    app = FetchApplication(config, store=StateStore(tmp_path))

    class FakeClient:
        def get_text(self, url: str) -> str:
            return "<html><h1>maintenance</h1></html>"

    result = app._fetch_one(
        FakeClient(),
        "https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html",
        "ne2026090512345",
        {"json"},
    )

    assert result.status == "source_contract_changed"
