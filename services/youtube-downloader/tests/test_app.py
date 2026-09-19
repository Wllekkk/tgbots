import logging
import sys
from io import StringIO
from pathlib import Path

import pytest

import youtube_downloader.app as app_module
from youtube_downloader.app import (
    _LOG_FORMAT,
    UPDATE_QUEUE_MAXSIZE,
    _RedactingFormatter,
    build_application,
    configure_logging,
)
from youtube_downloader.config import Settings


def test_dependency_logs_that_can_contain_token_are_suppressed() -> None:
    configure_logging("DEBUG", "123456:secret-token")

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("telegram").getEffectiveLevel() >= logging.WARNING


def test_log_formatter_redacts_token_from_messages_and_exceptions() -> None:
    token = "123456:secret-token"
    try:
        raise RuntimeError(f"request to /bot{token}/getMe failed")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="telegram.ext.ExtBot",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="API URL: %s",
        args=(f"https://api.telegram.org/bot{token}/getMe",),
        exc_info=exc_info,
    )
    rendered = _RedactingFormatter(_LOG_FORMAT, (token,)).format(record)

    assert token not in rendered
    assert rendered.count("[REDACTED]") == 2


def test_application_uses_bounded_update_queue(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        monkeypatch.delenv(name, raising=False)

    application = build_application(
        Settings.from_env(
            {
                "BOT_TOKEN": "123456:test-token",
                "ALLOWED_TELEGRAM_USER_ID": "42",
                "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            }
        )
    )

    assert application.update_queue.maxsize == UPDATE_QUEUE_MAXSIZE


def test_fatal_error_is_logged_without_exposing_token(
    tmp_path: Path, monkeypatch
) -> None:
    token = "123456:secret-token"
    settings = Settings.from_env(
        {
            "BOT_TOKEN": token,
            "ALLOWED_TELEGRAM_USER_ID": "42",
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
            "LOG_LEVEL": "DEBUG",
        }
    )

    class FatalBotError(RuntimeError):
        pass

    class Application:
        def run_polling(self, **kwargs):
            raise FatalBotError(f"The token `{token}` was rejected")

    monkeypatch.setattr(
        app_module.Settings,
        "from_env",
        classmethod(lambda cls: settings),
    )
    monkeypatch.setattr(app_module, "build_application", lambda current: Application())

    stream = StringIO()
    root_logger = logging.getLogger()
    previous_handlers = root_logger.handlers[:]
    previous_level = root_logger.level
    root_logger.handlers[:] = [logging.StreamHandler(stream)]
    try:
        with pytest.raises(SystemExit) as captured:
            app_module.main()
    finally:
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)

    assert captured.value.code == 1
    assert token not in stream.getvalue()
    assert "FatalBotError" in stream.getvalue()
    assert "[REDACTED]" in stream.getvalue()
