from datetime import date

from nhk_easy_fetcher.discovery import article_id_to_date, discover_article_urls


def test_keeps_only_easily_identified_article_urls(load_fixture) -> None:
    articles = discover_article_urls(load_fixture("easy_sitemap.xml"))
    assert [article.article_id for article in articles] == [
        "ne2026090512345",
        "ne2026090410000",
    ]


def test_filters_to_requested_japan_business_date(load_fixture) -> None:
    articles = discover_article_urls(
        load_fixture("easy_sitemap.xml"), only_date=date(2026, 9, 5)
    )
    assert len(articles) == 1
    assert articles[0].article_id == "ne2026090512345"


def test_article_id_to_date() -> None:
    assert article_id_to_date("ne2026090512345") == date(2026, 9, 5)
