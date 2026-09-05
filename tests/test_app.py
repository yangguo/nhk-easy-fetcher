from pathlib import Path

import respx
from typer.testing import CliRunner

from nhk_easy_fetcher.app import FetchApplication
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
