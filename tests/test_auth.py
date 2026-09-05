from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nhk_easy_fetcher.auth import CookieJarProvider, FixtureAuthProvider, NoAuthProvider
from nhk_easy_fetcher.errors import AuthorizationUnavailable, InsecureCredentialStore


def test_no_auth_provider_never_invents_credentials() -> None:
    now = datetime.now(tz=ZoneInfo("UTC"))
    assert NoAuthProvider().get_session(now).cookies == {}


def test_cookie_jar_provider_refuses_world_readable_file(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text('{"session": "abc"}', encoding="utf-8")
    cookie_file.chmod(0o644)
    with pytest.raises(InsecureCredentialStore):
        CookieJarProvider(cookie_file).get_session(datetime.now(tz=ZoneInfo("UTC")))


def test_cookie_jar_provider_loads_secure_file(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text('{"cookies": {"nhk_session": "value"}}', encoding="utf-8")
    cookie_file.chmod(0o600)
    session = CookieJarProvider(cookie_file).get_session(datetime.now(tz=ZoneInfo("UTC")))
    assert session.cookies["nhk_session"] == "value"


def test_fixture_auth_provider() -> None:
    provider = FixtureAuthProvider({"a": "b"})
    session = provider.get_session(datetime.now(tz=ZoneInfo("UTC")))
    assert session.cookies == {"a": "b"}


def test_cookie_jar_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationUnavailable):
        CookieJarProvider(tmp_path / "missing.json").get_session(
            datetime.now(tz=ZoneInfo("UTC"))
        )
