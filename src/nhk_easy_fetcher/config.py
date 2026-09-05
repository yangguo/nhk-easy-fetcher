"""Configuration loading and validation."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class FetchConfig(BaseModel):
    timezone: str = "Asia/Tokyo"
    request_timeout_seconds: float = 20
    max_retries: int = 2
    min_interval_seconds: float = 1.5
    max_concurrency: int = 1
    allow_partial: bool = False

    @field_validator("max_concurrency")
    @classmethod
    def concurrency_must_be_one(cls, value: int) -> int:
        if value > 1:
            raise ValueError("max_concurrency must be 1 in v0")
        return value

    @field_validator("request_timeout_seconds", "min_interval_seconds")
    @classmethod
    def must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be positive")
        return value


class DiscoveryConfig(BaseModel):
    sitemap_url: str = "https://news.web.nhk/news/easy/sitemap/sitemap.xml"
    lookback_days: int = 3


class AuthConfig(BaseModel):
    provider: Literal["none", "cookie_jar"] = "none"
    cookie_jar_path: Path = Path("~/.nhk-easy-fetcher/auth/cookies.json")
    cache_until_expiry: bool = True


class AudioConfig(BaseModel):
    mode: Literal["off", "m4a", "mp3", "manifest"] = "off"
    ffmpeg_path: str = "ffmpeg"
    keep_manifest: bool = False


class StorageConfig(BaseModel):
    output_dir: Path = Path("~/NHK-Easy")
    keep_raw_response: bool = False


class AppConfig(BaseModel):
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


def _expand_path(path: Path) -> Path:
    return path.expanduser().resolve()


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path
    if config_path is None:
        env_path = os.environ.get("NHK_EASY_CONFIG")
        if env_path:
            config_path = Path(env_path)

    if config_path is not None and config_path.exists():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
        config = AppConfig.model_validate(data)
    else:
        config = AppConfig()

    config.auth.cookie_jar_path = _expand_path(config.auth.cookie_jar_path)
    config.storage.output_dir = _expand_path(config.storage.output_dir)

    ffmpeg_env = os.environ.get("NHK_EASY_FFMPEG_PATH")
    if ffmpeg_env:
        config.audio.ffmpeg_path = ffmpeg_env

    return config
