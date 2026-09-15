import httpx
import pytest
import respx

from nhk_easy_fetcher.client import HttpClient
from nhk_easy_fetcher.config import FetchConfig
from nhk_easy_fetcher.errors import AuthorizationUnavailable, RateLimited


@respx.mock
def test_requests_carry_browser_like_default_headers() -> None:
    """NHK's edge (2026-09) 403s non-browser UA/header sets; verify we match a browser."""
    route = respx.get("https://example.test/article").respond(200, text="ok")
    with HttpClient(FetchConfig(min_interval_seconds=0.01)) as client:
        assert client.get_text("https://example.test/article") == "ok"
    sent = route.calls.last.request.headers
    assert sent["user-agent"].startswith("Mozilla/5.0")
    assert "text/html" in sent["accept"]
    assert sent["accept-language"].startswith("ja")


@respx.mock
def test_401_is_not_retried_as_a_network_error() -> None:
    route = respx.get("https://example.test/article").respond(401)
    with HttpClient(FetchConfig(max_retries=2)) as client:
        with pytest.raises(AuthorizationUnavailable):
            client.get_text("https://example.test/article")
    assert route.call_count == 1


@respx.mock
def test_503_is_retried_only_up_to_configured_limit() -> None:
    route = respx.get("https://example.test/article").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, text="ok"),
        ]
    )
    with HttpClient(FetchConfig(max_retries=2, min_interval_seconds=0.01)) as client:
        assert client.get_text("https://example.test/article") == "ok"
    assert route.call_count == 2


@respx.mock
def test_429_stops_without_retrying() -> None:
    route = respx.get("https://example.test/rate").respond(
        429,
        headers={"Retry-After": "0"},
    )
    with HttpClient(FetchConfig(max_retries=2, min_interval_seconds=0.01)) as client:
        with pytest.raises(RateLimited):
            client.get_text("https://example.test/rate")
    assert route.call_count == 1
