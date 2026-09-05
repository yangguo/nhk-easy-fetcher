"""Detect whether an HTML response is a complete classic EASY article."""

from selectolax.parser import HTMLParser

from nhk_easy_fetcher.models import PageMode


def detect_page_mode(html: str) -> PageMode:
    tree = HTMLParser(html)
    if tree.css_first("#js-article-body") is not None:
        return PageMode.CLASSIC_COMPLETE
    if "NHK ONE" in tree.text() or tree.css_first("script#__NEXT_DATA__") is not None:
        return PageMode.NEXT_PARTIAL
    if "self.__next_f" in html or 'data-erpccontent="SinglePageERPCContent' in html:
        return PageMode.NEXT_PARTIAL
    return PageMode.UNKNOWN
