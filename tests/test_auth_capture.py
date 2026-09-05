from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from nhk_easy_fetcher import auth_capture
from nhk_easy_fetcher.auth_capture import (
    build_cookie_jar_payload,
    extract_z_at,
    filter_nhk_cookies,
)
from nhk_easy_fetcher.errors import AuthorizationUnavailable


def test_filter_keeps_only_nhk_domain_cookies() -> None:
    cookies = [
        {"name": "a", "value": "1", "domain": ".news.web.nhk"},
        {"name": "b", "value": "2", "domain": "news.web.nhk"},
        {"name": "tracker", "value": "x", "domain": ".example.com"},
        {"name": "empty", "value": "", "domain": ".news.web.nhk"},
    ]
    assert filter_nhk_cookies(cookies) == {"a": "1", "b": "2"}


def test_filter_keeps_parent_domain_cookies() -> None:
    """Live-verified: consent sets cookies on `.web.nhk`, not `news.web.nhk`."""
    cookies = [
        {"name": "z_at", "value": "tok", "domain": ".web.nhk"},
        {"name": "bff-rt-authz", "value": "h", "domain": ".web.nhk"},
        {"name": "spoof", "value": "x", "domain": ".evilweb.nhk"},
    ]
    assert filter_nhk_cookies(cookies) == {"z_at": "tok", "bff-rt-authz": "h"}


def test_extract_z_at_from_storage_state() -> None:
    state = {
        "origins": [
            {
                "origin": "https://news.web.nhk",
                "localStorage": [{"name": "z_at", "value": "tok123"}],
            }
        ]
    }
    assert extract_z_at(state) == "tok123"


def test_extract_z_at_missing_returns_none() -> None:
    assert extract_z_at({"origins": []}) is None
    assert extract_z_at({}) is None


def test_extract_z_at_ignores_non_nhk_origins() -> None:
    state = {
        "origins": [
            {
                "origin": "https://attacker.example",
                "localStorage": [{"name": "z_at", "value": "wrong"}],
            }
        ]
    }
    assert extract_z_at(state) is None


def test_build_payload_with_and_without_z_at() -> None:
    assert build_cookie_jar_payload({"a": "1"}, "tok") == {
        "cookies": {"a": "1", "z_at": "tok"},
        "headers": {},
    }
    assert build_cookie_jar_payload({"a": "1"}, None) == {
        "cookies": {"a": "1"},
        "headers": {},
    }


def test_write_cookie_jar_enforces_permissions(tmp_path: Path) -> None:
    import json
    import os

    from nhk_easy_fetcher.auth_capture import write_cookie_jar

    jar = tmp_path / "sub" / "cookies.json"
    out = write_cookie_jar(jar, {"cookies": {"a": "1"}, "headers": {}})
    assert out == jar.expanduser()
    assert json.loads(jar.read_text(encoding="utf-8"))["cookies"] == {"a": "1"}
    assert oct(os.stat(jar).st_mode & 0o777) == "0o600"


def test_write_cookie_jar_does_not_follow_predictable_temp_symlink(tmp_path: Path) -> None:
    from nhk_easy_fetcher.auth_capture import write_cookie_jar

    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    (tmp_path / "cookies.tmp").symlink_to(victim)

    write_cookie_jar(tmp_path / "cookies.json", {"cookies": {"a": "1"}, "headers": {}})

    assert victim.read_text(encoding="utf-8") == "keep"


class FakeContext:
    def __init__(self, cookies: list[dict[str, Any]], state: dict[str, Any]) -> None:
        self._cookies = cookies
        self._state = state

    def cookies(self) -> list[dict[str, Any]]:
        return self._cookies

    def storage_state(self) -> dict[str, Any]:
        return self._state


def _ctx(cookies: list[dict[str, Any]], state: dict[str, Any]) -> Any:
    @contextmanager
    def _open() -> Iterator[FakeContext]:
        yield FakeContext(cookies, state)

    return _open


def test_run_capture_writes_jar(tmp_path: Path) -> None:
    import json

    jar = tmp_path / "cookies.json"
    out = auth_capture.run_capture(
        jar,
        open_context=_ctx(
            [{"name": "a", "value": "1", "domain": ".news.web.nhk"}],
            {
                "origins": [
                    {
                        "origin": "https://news.web.nhk",
                        "localStorage": [{"name": "z_at", "value": "t"}],
                    }
                ]
            },
        ),
        wait_for_user=lambda timeout: True,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["cookies"] == {"a": "1", "z_at": "t"}
    assert payload["headers"] == {}


def test_run_capture_takes_z_at_from_cookie(tmp_path: Path) -> None:
    """Live-verified: z_at arrives as a `.web.nhk` cookie, not localStorage."""
    import json

    jar = tmp_path / "cookies.json"
    out = auth_capture.run_capture(
        jar,
        open_context=_ctx(
            [
                {"name": "z_at", "value": "ctok", "domain": ".web.nhk"},
                {"name": "bff-rt-authz", "value": "h", "domain": ".web.nhk"},
            ],
            {"origins": []},
        ),
        wait_for_user=lambda timeout: True,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["cookies"] == {"z_at": "ctok", "bff-rt-authz": "h"}
    assert payload["headers"] == {}


def test_run_capture_empty_session_raises(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationUnavailable):
        auth_capture.run_capture(
            tmp_path / "cookies.json",
            open_context=_ctx([], {"origins": []}),
            wait_for_user=lambda timeout: True,
        )


def test_run_capture_timeout_raises(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationUnavailable):
        auth_capture.run_capture(
            tmp_path / "cookies.json",
            open_context=_ctx([], {"origins": []}),
            wait_for_user=lambda timeout: False,
        )


def test_open_chrome_context_closes_on_goto_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import types

    closed = {"count": 0}

    class FakePage:
        def goto(self, url: str) -> None:
            raise RuntimeError("boom")

    class FakeLaunchContext:
        def __init__(self) -> None:
            self.pages: list[Any] = []

        def new_page(self) -> FakePage:
            return FakePage()

        def close(self) -> None:
            closed["count"] += 1

    fake_context = FakeLaunchContext()

    class FakeChromium:
        def launch_persistent_context(self, *args: Any, **kwargs: Any) -> Any:
            return fake_context

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    @contextmanager
    def _sync_playwright() -> Iterator[Any]:
        yield FakePlaywright()

    fake_pkg = types.ModuleType("playwright")
    fake_mod = types.ModuleType("playwright.sync_api")
    fake_mod.sync_playwright = _sync_playwright  # type: ignore[attr-defined]
    fake_pkg.sync_api = fake_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)

    with pytest.raises(RuntimeError, match="boom"):
        with auth_capture.open_chrome_context("/tmp/fake-profile"):
            pass
    assert closed["count"] == 1
