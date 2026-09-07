"""Authorization session providers."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nhk_easy_fetcher.errors import AuthorizationUnavailable, InsecureCredentialStore


@dataclass
class AuthorizedSession:
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: datetime | None = None


class AuthProvider(Protocol):
    def get_session(self, now: datetime) -> AuthorizedSession: ...

    def invalidate(self, reason: str) -> None: ...


class NoAuthProvider:
    def get_session(self, now: datetime) -> AuthorizedSession:
        return AuthorizedSession()

    def invalidate(self, reason: str) -> None:
        return None


class CookieJarProvider:
    """Load cookies from a user-provided, permission-restricted JSON file."""

    def __init__(self, cookie_jar_path: Path) -> None:
        self._path = cookie_jar_path
        self._invalidated = False

    def _check_permissions(self) -> None:
        if not self._path.exists():
            raise AuthorizationUnavailable(
                f"Cookie jar not found at {self._path}. "
                "Export browser cookies for news.web.nhk after completing NHK ONE consent."
            )
        mode = self._path.stat().st_mode
        # Windows reports synthetic POSIX mode bits (regular files show 0o666)
        # and protects files via ACLs instead, so the group/other check only
        # applies on POSIX platforms.
        if os.name != "nt" and mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
            raise InsecureCredentialStore(
                f"Cookie jar {self._path} must not be world- or group-readable (chmod 600)"
            )

    def get_session(self, now: datetime) -> AuthorizedSession:
        if self._invalidated:
            raise AuthorizationUnavailable("Cached session was invalidated")
        self._check_permissions()
        data = json.loads(self._path.read_text(encoding="utf-8"))
        cookies: dict[str, str] = {}
        headers: dict[str, str] = {}

        if isinstance(data, dict):
            if "cookies" in data and isinstance(data["cookies"], dict):
                cookies = {str(k): str(v) for k, v in data["cookies"].items()}
            else:
                cookies = {str(k): str(v) for k, v in data.items() if not k.startswith("_")}
            if "headers" in data and isinstance(data["headers"], dict):
                headers = {str(k): str(v) for k, v in data["headers"].items()}

        if not cookies and not headers:
            raise AuthorizationUnavailable(
                "Cookie jar is empty. Provide cookies from a browser session with NHK ONE consent."
            )
        return AuthorizedSession(cookies=cookies, headers=headers)

    def invalidate(self, reason: str) -> None:
        self._invalidated = True


class FixtureAuthProvider:
    """Offline test provider with fixed cookies."""

    def __init__(self, cookies: dict[str, str] | None = None) -> None:
        self._cookies = cookies or {}

    def get_session(self, now: datetime) -> AuthorizedSession:
        return AuthorizedSession(cookies=dict(self._cookies))

    def invalidate(self, reason: str) -> None:
        return None
