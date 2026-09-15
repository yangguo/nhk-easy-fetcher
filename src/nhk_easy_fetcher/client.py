"""HTTP client with cautious retry and rate limiting."""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from nhk_easy_fetcher.config import FetchConfig
from nhk_easy_fetcher.errors import (
    AuthorizationUnavailable,
    NetworkTransient,
    RateLimited,
    RemoteTransient,
)

RETRYABLE_STATUS_CODES = {502, 503, 504}
# NHK's edge (live-checked 2026-09-15) rejects non-browser User-Agents with 403,
# and a browser UA alone is not enough: at least one standard browser header
# (Accept / Accept-Language) must accompany it.  These are the minimal headers a
# normal browser sends; no cookies or fingerprinting headers are added here.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
DEFAULT_ACCEPT_LANGUAGE = "ja,en-US;q=0.9,en;q=0.8"


class HttpClient:
    def __init__(
        self,
        config: FetchConfig,
        *,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._config = config
        self._last_request_at = 0.0
        extra_headers = headers or {}
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=config.request_timeout_seconds,
                read=config.request_timeout_seconds,
                write=config.request_timeout_seconds,
                pool=config.request_timeout_seconds,
            ),
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": DEFAULT_ACCEPT,
                "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
                **extra_headers,
            },
            cookies=cookies or {},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _wait_interval(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = self._config.min_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    def _handle_status(self, response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise AuthorizationUnavailable(f"HTTP {response.status_code}")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            seconds = float(retry_after) if retry_after else None
            raise RateLimited(retry_after=seconds)
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise RemoteTransient(f"HTTP {response.status_code}")
        response.raise_for_status()

    def get_text(self, url: str) -> str:
        attempts = self._config.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            self._wait_interval()
            try:
                response = self._client.get(url)
                self._last_request_at = time.monotonic()
                self._handle_status(response)
                return response.text
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = NetworkTransient(str(exc))
            except RateLimited:
                # A 429 is an explicit server-side pacing signal.  Surface it
                # immediately rather than multiplying the request pressure.
                raise
            except RemoteTransient as exc:
                last_error = exc
            except AuthorizationUnavailable:
                raise

            if attempt < attempts - 1:
                backoff = min(2**attempt + random.uniform(0, 0.5), 10)
                if isinstance(last_error, RateLimited) and last_error.retry_after:
                    backoff = min(last_error.retry_after, 60)
                time.sleep(backoff)

        if last_error:
            raise last_error
        raise NetworkTransient("Request failed")
