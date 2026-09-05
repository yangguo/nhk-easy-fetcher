"""Capture NHK ONE consent cookies from the user's own installed Chrome.

Only entry that knows about Playwright. Everything else talks to the
cookie-jar file via CookieJarProvider.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

CONSENT_URL = "https://news.web.nhk/news/easy/"
# Consent cookies are set on the parent domain `.web.nhk` (live-verified
# 2026-09-05: z_at, bff-rt-authz, ...), which browsers also send to subdomains
# such as news.web.nhk. Keep the suffix at web.nhk, not news.web.nhk.
NHK_DOMAIN_SUFFIX = "web.nhk"


def filter_nhk_cookies(
    cookies: list[dict[str, Any]], domain_suffix: str = NHK_DOMAIN_SUFFIX
) -> dict[str, str]:
    """Keep non-empty cookies whose domain is the NHK suffix (or its subdomains)."""
    kept: dict[str, str] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        name = str(cookie.get("name", ""))
        value = str(cookie.get("value", ""))
        if not name or not value:
            continue
        if domain == domain_suffix or domain.endswith("." + domain_suffix):
            kept[name] = value
    return kept


def extract_z_at(storage_state: dict[str, Any]) -> str | None:
    """Return localStorage z_at for the mediatoken Authorization header, if present."""
    origins = storage_state.get("origins")
    if not isinstance(origins, list):
        return None
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        hostname = urlparse(str(origin.get("origin", ""))).hostname or ""
        if hostname != NHK_DOMAIN_SUFFIX and not hostname.endswith("." + NHK_DOMAIN_SUFFIX):
            continue
        entries = origin.get("localStorage")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name") == "z_at":
                value = str(entry.get("value", ""))
                return value or None
    return None


def build_cookie_jar_payload(
    cookies: dict[str, str], z_at: str | None
) -> dict[str, Any]:
    """Build the CookieJarProvider-compatible payload (values stay in memory only)."""
    captured = dict(cookies)
    if z_at and "z_at" not in captured:
        # The audio layer deliberately obtains the bearer token from the
        # credential map instead of forwarding Authorization to every NHK page.
        captured["z_at"] = z_at
    return {"cookies": captured, "headers": {}}


def write_cookie_jar(path: Path, payload: dict[str, Any]) -> Path:
    """Atomically write the jar with 0700 parent dir and 0600 file perms."""
    import json

    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    descriptor, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    tmp = Path(tmp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


class CaptureContext(Protocol):
    def cookies(self) -> list[dict[str, Any]]: ...
    def storage_state(self) -> dict[str, Any]: ...


def wait_for_enter(timeout_seconds: float) -> bool:
    """Wait for Enter on stdin; True if pressed, False on timeout."""
    import select
    import sys

    sys.stderr.write("In the opened Chrome, complete consent, then press Enter here...\n")
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    except (OSError, ValueError, AttributeError):
        sys.stdin.readline()
        return True
    if not ready:
        return False
    sys.stdin.readline()
    return True


@contextmanager
def open_chrome_context(
    user_data_dir: str, consent_url: str = CONSENT_URL
) -> Iterator[Any]:
    """Launch installed Chrome via Playwright (channel='chrome'). Lazy import."""
    import importlib

    try:
        sync_api = importlib.import_module("playwright.sync_api")
    except ImportError as exc:
        from nhk_easy_fetcher.errors import BrowserUnavailable

        raise BrowserUnavailable(
            "Playwright is not installed. Run: pip install -e '.[browser]'"
        ) from exc
    from nhk_easy_fetcher.errors import BrowserUnavailable

    with sync_api.sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir,
                channel="chrome",
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:
            raise BrowserUnavailable(
                "Could not launch installed Google Chrome. "
                "Install Chrome and retry."
            ) from exc
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(consent_url)
        except Exception:
            context.close()
            raise
        try:
            yield context
        finally:
            context.close()


def run_capture(
    cookie_jar: Path,
    *,
    consent_url: str = CONSENT_URL,
    timeout_seconds: float = 300,
    open_context: Callable[[], AbstractContextManager[Any]] | None = None,
    wait_for_user: Callable[[float], bool] | None = None,
) -> Path:
    """Open Chrome, let the user consent, then auto-save the jar. Returns jar path."""
    import shutil
    import tempfile

    from nhk_easy_fetcher.errors import AuthorizationUnavailable

    wait = wait_for_user or wait_for_enter
    user_data_dir = tempfile.mkdtemp(prefix="nhk-easy-profile-")
    os.chmod(user_data_dir, 0o700)
    try:
        opener = open_context or (lambda: open_chrome_context(user_data_dir, consent_url))
        with opener() as context:
            if not wait(timeout_seconds):
                raise AuthorizationUnavailable(
                    "Timed out waiting for consent. Re-run and press Enter after consent."
                )
            raw_cookies = context.cookies()
            try:
                state = context.storage_state()
            except Exception:
                state = {}
            cookies = filter_nhk_cookies(raw_cookies)
            if not cookies:
                raise AuthorizationUnavailable(
                    "No web.nhk cookies captured. Complete consent and retry."
                )
            # z_at is set as a cookie (live-verified); localStorage is only a fallback.
            z_at = cookies.get("z_at") or extract_z_at(state)
            payload = build_cookie_jar_payload(cookies, z_at)
            return write_cookie_jar(cookie_jar, payload)
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)
