from nhk_easy_fetcher.article_ids import extract_article_id


def test_extract_legacy_article_id() -> None:
    url = "https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html"
    assert extract_article_id(url) == "ne2026090512345"


def test_extract_current_article_id() -> None:
    url = "https://news.web.nhk/news/easy/20260904de48127/20260904de48127.html"
    assert extract_article_id(url) == "20260904de48127"


def test_rejects_non_article_url() -> None:
    assert extract_article_id("https://news.web.nhk/news/easy/about/") is None
