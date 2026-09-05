from datetime import date

from nhk_easy_fetcher.article_ids import article_id_to_date, extract_article_id
from nhk_easy_fetcher.discovery import discover_article_urls


def test_keeps_only_easily_identified_article_urls(load_fixture) -> None:
    articles = discover_article_urls(load_fixture("easy_sitemap.xml"))
    assert [article.article_id for article in articles] == [
        "ne2026090512345",
        "ne2026090410000",
        "20260904de48127",
    ]


def test_filters_to_requested_japan_business_date(load_fixture) -> None:
    articles = discover_article_urls(load_fixture("easy_sitemap.xml"), only_date=date(2026, 9, 5))
    assert len(articles) == 1
    assert articles[0].article_id == "ne2026090512345"


def test_filters_current_id_format_by_date(load_fixture) -> None:
    articles = discover_article_urls(load_fixture("easy_sitemap.xml"), only_date=date(2026, 9, 4))
    assert {a.article_id for a in articles} == {"ne2026090410000", "20260904de48127"}


def test_article_id_to_date_legacy() -> None:
    assert article_id_to_date("ne2026090512345") == date(2026, 9, 5)


def test_article_id_to_date_current() -> None:
    assert article_id_to_date("20260904de48127") == date(2026, 9, 4)


def test_extract_article_id_current_format() -> None:
    url = "https://news.web.nhk/news/easy/20260904de48127/20260904de48127.html"
    assert extract_article_id(url) == "20260904de48127"


def test_sorts_mixed_article_id_formats_by_published_date() -> None:
    sitemap = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html</loc></url>
      <url><loc>https://news.web.nhk/news/easy/20260906de1/20260906de1.html</loc></url>
    </urlset>"""

    articles = discover_article_urls(sitemap)

    assert [article.article_id for article in articles] == ["20260906de1", "ne2026090512345"]
