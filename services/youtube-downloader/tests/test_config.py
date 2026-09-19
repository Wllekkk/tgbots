from pathlib import Path

import pytest

from youtube_downloader.config import (
    CLOUD_MAX_FILE_BYTES,
    ConfigurationError,
    Settings,
)

BASE_ENV = {
    "BOT_TOKEN": "123456:test-token",
    "ALLOWED_TELEGRAM_USER_ID": "42",
}


def test_defaults_are_safe(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {**BASE_ENV, "DOWNLOAD_DIR": str(tmp_path / "downloads")}
    )

    assert settings.allowed_user_id == 42
    assert settings.max_file_bytes == CLOUD_MAX_FILE_BYTES
    assert settings.max_duration_seconds == 7_200
    assert settings.download_dir == (tmp_path / "downloads").resolve()
    assert settings.telegram_local_mode is False
    assert "test-token" not in repr(settings)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BOT_TOKEN", ""),
        ("ALLOWED_TELEGRAM_USER_ID", ""),
        ("ALLOWED_TELEGRAM_USER_ID", "abc"),
        ("ALLOWED_TELEGRAM_USER_ID", "0"),
        ("MAX_FILE_BYTES", "0"),
        ("LOG_LEVEL", "verbose"),
    ],
)
def test_invalid_required_settings_fail_closed(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE_ENV, name: value})


def test_public_api_cannot_exceed_cloud_limit() -> None:
    with pytest.raises(ConfigurationError, match="public"):
        Settings.from_env({**BASE_ENV, "MAX_FILE_BYTES": str(CLOUD_MAX_FILE_BYTES + 1)})


def test_local_mode_requires_both_base_urls() -> None:
    with pytest.raises(ConfigurationError, match="requires"):
        Settings.from_env({**BASE_ENV, "TELEGRAM_LOCAL_MODE": "true"})


def test_local_mode_accepts_larger_limit() -> None:
    settings = Settings.from_env(
        {
            **BASE_ENV,
            "TELEGRAM_LOCAL_MODE": "true",
            "TELEGRAM_API_BASE_URL": "http://telegram-api:8081/bot",
            "TELEGRAM_FILE_BASE_URL": "http://telegram-api:8081/file/bot",
            "MAX_FILE_BYTES": "100000000",
        }
    )

    assert settings.telegram_local_mode is True
    assert settings.max_file_bytes == 100_000_000


def test_base_urls_must_be_configured_as_a_pair() -> None:
    with pytest.raises(ConfigurationError, match="must be set together"):
        Settings.from_env(
            {
                **BASE_ENV,
                "TELEGRAM_API_BASE_URL": "https://example.test/bot",
            }
        )


def test_custom_base_urls_require_local_mode() -> None:
    with pytest.raises(ConfigurationError, match="LOCAL_MODE=true"):
        Settings.from_env(
            {
                **BASE_ENV,
                "TELEGRAM_API_BASE_URL": "http://telegram-api:8081/bot",
                "TELEGRAM_FILE_BASE_URL": "http://telegram-api:8081/file/bot",
            }
        )


def test_download_directory_cannot_be_filesystem_root() -> None:
    with pytest.raises(ConfigurationError, match="filesystem root"):
        Settings.from_env({**BASE_ENV, "DOWNLOAD_DIR": "/"})


def test_base_url_rejects_invalid_port() -> None:
    with pytest.raises(ConfigurationError, match="valid Bot API"):
        Settings.from_env(
            {
                **BASE_ENV,
                "TELEGRAM_API_BASE_URL": "http://telegram-api:not-a-port/bot",
                "TELEGRAM_FILE_BASE_URL": "http://telegram-api:8081/file/bot",
            }
        )
