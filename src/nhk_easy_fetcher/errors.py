"""Typed errors for fetch operations."""


class NhkEasyFetcherError(Exception):
    """Base error for the fetcher."""


class NetworkTransient(NhkEasyFetcherError):
    """Transient network failure (timeout, DNS, connection reset)."""


class RemoteTransient(NhkEasyFetcherError):
    """Transient remote server error (502/503/504)."""


class RateLimited(NhkEasyFetcherError):
    """Rate limited by remote server (429)."""

    def __init__(self, message: str = "Rate limited", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthorizationUnavailable(NhkEasyFetcherError):
    """Authorization required or session expired (401/403 or consent page)."""


class FullContentUnavailable(NhkEasyFetcherError):
    """Classic article body is not present in the response."""

    def __init__(self, source_url: str, page_mode: str = "next_partial") -> None:
        super().__init__(f"Full article content unavailable at {source_url} (mode={page_mode})")
        self.source_url = source_url
        self.page_mode = page_mode


class SourceContractChanged(NhkEasyFetcherError):
    """Site structure changed; selectors or schema no longer match."""


class ParseSuspect(NhkEasyFetcherError):
    """Parsed content failed integrity checks."""


class InsecureCredentialStore(NhkEasyFetcherError):
    """Cookie or credential file has insecure permissions."""


class AudioUnavailable(NhkEasyFetcherError):
    """Audio manifest or download unavailable."""


class LocalWriteFailed(NhkEasyFetcherError):
    """Failed to write local artifacts."""
