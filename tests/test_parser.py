import pytest

from nhk_easy_fetcher.errors import FullContentUnavailable
from nhk_easy_fetcher.parser import parse_complete_article


def test_parser_keeps_ruby_and_builds_plain_and_reading_views(load_fixture) -> None:
    article = parse_complete_article(
        load_fixture("classic_complete.html"),
        source_url="https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html",
    )
    assert article.title.plain == "漢字のニュース"
    assert article.paragraphs[0].plain == "漢字を読む。"
    assert article.paragraphs[0].with_readings == "漢字（かんじ）を読む。"
    assert article.article_id == "ne2026090512345"
    assert len(article.paragraphs) == 2


def test_parser_rejects_partial_page(load_fixture) -> None:
    with pytest.raises(FullContentUnavailable):
        parse_complete_article(
            load_fixture("next_partial.html"),
            source_url="https://news.web.nhk/news/easy/ne2026090512345/ne2026090512345.html",
        )
