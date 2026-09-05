
from nhk_easy_fetcher.config import FetchConfig, load_config
from nhk_easy_fetcher.models import ContentStatus, PageMode


def test_default_config_is_low_rate_and_disallows_partial() -> None:
    config = FetchConfig()
    assert config.max_concurrency == 1
    assert config.min_interval_seconds >= 1.0
    assert config.allow_partial is False


def test_complete_status_requires_classic_page_mode() -> None:
    assert ContentStatus.for_page_mode(PageMode.CLASSIC_COMPLETE).value == "complete"
    assert ContentStatus.for_page_mode(PageMode.NEXT_PARTIAL).value == "unavailable"


def test_load_config_defaults() -> None:
    config = load_config()
    assert config.discovery.sitemap_url.endswith("sitemap.xml")
