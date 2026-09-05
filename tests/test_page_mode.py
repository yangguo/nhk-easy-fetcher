from nhk_easy_fetcher.models import PageMode
from nhk_easy_fetcher.page_mode import detect_page_mode


def test_detects_classic_page_only_when_full_body_selector_exists(load_fixture) -> None:
    html = load_fixture("classic_complete.html")
    assert detect_page_mode(html) is PageMode.CLASSIC_COMPLETE


def test_does_not_mistake_nhk_one_shell_for_complete_article(load_fixture) -> None:
    html = load_fixture("next_partial.html")
    assert detect_page_mode(html) is PageMode.NEXT_PARTIAL


def test_unknown_page_is_not_complete(load_fixture) -> None:
    html = load_fixture("unknown.html")
    assert detect_page_mode(html) is PageMode.UNKNOWN
