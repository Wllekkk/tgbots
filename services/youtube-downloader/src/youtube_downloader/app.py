from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from youtube_downloader.config import Settings
from youtube_downloader.handlers import BotHandlers
from youtube_downloader.ytdlp import YtDlpService

LOGGER = logging.getLogger(__name__)
UPDATE_QUEUE_MAXSIZE = 32
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: tuple[str, ...]) -> None:
        super().__init__(fmt)
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        return rendered


def configure_logging(level: str, bot_token: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format=_LOG_FORMAT,
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level))
    formatter = _RedactingFormatter(_LOG_FORMAT, (bot_token,))
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

    # HTTPX includes the full Bot API URL (and therefore the token) in INFO logs.
    for logger_name in ("httpx", "httpcore", "telegram"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def build_application(settings: Settings) -> Application:
    downloader = YtDlpService(
        download_dir=settings.download_dir,
        max_file_bytes=settings.max_file_bytes,
        max_duration_seconds=settings.max_duration_seconds,
        metadata_timeout_seconds=settings.metadata_timeout_seconds,
        download_timeout_seconds=settings.download_timeout_seconds,
    )
    downloader.cleanup_stale_jobs()
    handlers = BotHandlers(settings, downloader)

    builder = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .update_queue(asyncio.Queue(maxsize=UPDATE_QUEUE_MAXSIZE))
        .concurrent_updates(False)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .media_write_timeout(300)
    )
    if settings.telegram_api_base_url is not None:
        builder = builder.base_url(settings.telegram_api_base_url).base_file_url(
            settings.telegram_file_base_url
        )
    if settings.telegram_local_mode:
        builder = builder.local_mode(True)

    application = builder.build()
    application.add_handler(CommandHandler(["start", "help"], handlers.start))
    application.add_handler(
        CallbackQueryHandler(handlers.handle_choice, pattern=r"^dl:")
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_url)
    )
    application.add_error_handler(handlers.handle_error)
    return application


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.bot_token)
    try:
        application = build_application(settings)
        application.run_polling(
            allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
            drop_pending_updates=True,
        )
    except Exception as exc:
        LOGGER.critical("Fatal bot error (%s)", type(exc).__name__, exc_info=True)
        raise SystemExit(1) from None
