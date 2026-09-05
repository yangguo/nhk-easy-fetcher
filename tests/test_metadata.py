from nhk_easy_fetcher.metadata import (
    extract_voice_uri_from_html,
    parse_top_list_voice_map,
    resolve_voice_uri,
)


def test_parse_top_list_voice_map(load_fixture) -> None:
    mapping = parse_top_list_voice_map(load_fixture("top_list.json"))
    assert mapping["ne2026090512345"] == "voice-20260905.mp4"
    assert mapping["20260904de48127"] == "voice-20260904de48127.mp4"


def test_extract_voice_uri_from_html() -> None:
    html = '<script>{"news_easy_voice_uri":"voice-from-page.mp4"}</script>'
    assert extract_voice_uri_from_html(html) == "voice-from-page.mp4"


def test_resolve_voice_uri_prefers_html() -> None:
    html = '{"news_easy_voice_uri":"voice-from-page.mp4"}'
    top_list = {"ne2026090512345": "voice-from-list.mp4"}
    assert (
        resolve_voice_uri(article_id="ne2026090512345", html=html, top_list_map=top_list)
        == "voice-from-page.mp4"
    )


def test_resolve_voice_uri_falls_back_to_top_list() -> None:
    top_list = {"20260904de48127": "voice-20260904de48127.mp4"}
    assert (
        resolve_voice_uri(article_id="20260904de48127", html="<html></html>", top_list_map=top_list)
        == "voice-20260904de48127.mp4"
    )
