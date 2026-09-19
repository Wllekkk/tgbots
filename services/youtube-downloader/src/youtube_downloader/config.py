from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

CLOUD_MAX_FILE_BYTES = 49_000_000
LOCAL_MAX_FILE_BYTES = 2_000_000_000


class ConfigurationError(ValueError):
    """Raised when required configuration is absent or unsafe."""


def _required(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _positive_int(env: dict[str, str], name: str, default: int | None = None) -> int:
    raw = env.get(name)
    if raw is None and default is not None:
        return default
    if raw is None or not raw.strip():
        raise ConfigurationError(f"{name} is required")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _boolean(env: dict[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _optional_base_url(env: dict[str, str], name: str) -> str | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        _ = parts.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} is not a valid Bot API base URL") from exc
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ConfigurationError(f"{name} is not a valid Bot API base URL")
    return raw.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str = field(repr=False)
    allowed_user_id: int
    download_dir: Path
    max_file_bytes: int
    max_duration_seconds: int
    download_timeout_seconds: int
    metadata_timeout_seconds: int
    pending_request_ttl_seconds: int
    log_level: str
    telegram_api_base_url: str | None = None
    telegram_file_base_url: str | None = None
    telegram_local_mode: bool = False

    @classmethod
    def from_env(cls, source: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if source is None else source)
        token = _required(env, "BOT_TOKEN")
        allowed_user_id = _positive_int(env, "ALLOWED_TELEGRAM_USER_ID")
        local_mode = _boolean(env, "TELEGRAM_LOCAL_MODE", False)
        api_base_url = _optional_base_url(env, "TELEGRAM_API_BASE_URL")
        file_base_url = _optional_base_url(env, "TELEGRAM_FILE_BASE_URL")

        if (api_base_url is None) != (file_base_url is None):
            raise ConfigurationError(
                "TELEGRAM_API_BASE_URL and TELEGRAM_FILE_BASE_URL must be set together"
            )
        if local_mode and api_base_url is None:
            raise ConfigurationError(
                "TELEGRAM_LOCAL_MODE requires both Telegram Bot API base URLs"
            )
        if not local_mode and api_base_url is not None:
            raise ConfigurationError(
                "Custom Telegram Bot API base URLs require TELEGRAM_LOCAL_MODE=true"
            )

        max_file_bytes = _positive_int(env, "MAX_FILE_BYTES", CLOUD_MAX_FILE_BYTES)
        size_limit = LOCAL_MAX_FILE_BYTES if local_mode else CLOUD_MAX_FILE_BYTES
        if max_file_bytes > size_limit:
            mode_name = "local" if local_mode else "public"
            raise ConfigurationError(
                f"MAX_FILE_BYTES exceeds the supported {mode_name} Bot API limit"
            )

        log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL is invalid")

        download_dir = (
            Path(env.get("DOWNLOAD_DIR", "/data/downloads")).expanduser().resolve()
        )
        if download_dir == Path(download_dir.anchor):
            raise ConfigurationError("DOWNLOAD_DIR must not be a filesystem root")

        return cls(
            bot_token=token,
            allowed_user_id=allowed_user_id,
            download_dir=download_dir,
            max_file_bytes=max_file_bytes,
            max_duration_seconds=_positive_int(env, "MAX_DURATION_SECONDS", 7_200),
            download_timeout_seconds=_positive_int(
                env, "DOWNLOAD_TIMEOUT_SECONDS", 900
            ),
            metadata_timeout_seconds=_positive_int(env, "METADATA_TIMEOUT_SECONDS", 45),
            pending_request_ttl_seconds=_positive_int(
                env, "PENDING_REQUEST_TTL_SECONDS", 600
            ),
            log_level=log_level,
            telegram_api_base_url=api_base_url,
            telegram_file_base_url=file_base_url,
            telegram_local_mode=local_mode,
        )
