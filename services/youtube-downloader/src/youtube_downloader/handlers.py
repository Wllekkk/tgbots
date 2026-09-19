from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from youtube_downloader.config import Settings
from youtube_downloader.jobs import PendingJobStore
from youtube_downloader.models import (
    DownloadChoice,
    DownloadError,
    DownloadTimeoutError,
    FileTooLargeError,
    MetadataError,
    UnsupportedMediaError,
)
from youtube_downloader.urls import InvalidYouTubeUrl, normalize_youtube_url
from youtube_downloader.ytdlp import YtDlpService

LOGGER = logging.getLogger(__name__)
_CALLBACK = re.compile(r"^dl:([A-Za-z0-9_-]{6,32}):(audio|video360|video720)$")
_UNSAFE_FILENAME = re.compile(r"[\\/\x00-\x1f\x7f]+")


def is_authorized(update: Update, allowed_user_id: int) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    return bool(
        user is not None
        and chat is not None
        and user.id == allowed_user_id
        and chat.type == ChatType.PRIVATE
    )


def safe_filename(title: str, extension: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub(" ", title)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned:
        cleaned = "youtube-media"
    return f"{cleaned[:90].rstrip()}{extension}"


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3_600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class BotHandlers:
    def __init__(self, settings: Settings, downloader: YtDlpService) -> None:
        self.settings = settings
        self.downloader = downloader
        self.pending = PendingJobStore(settings.pending_request_ttl_seconds)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if not is_authorized(update, self.settings.allowed_user_id):
            return
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "傳送一個 YouTube 單一影片連結，我會讓你選擇 M4A、≤360p 或 ≤720p。\n\n"
                "目前不支援播放清單、直播或需要登入的影片。"
            )

    async def handle_url(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        del context
        if not is_authorized(update, self.settings.allowed_user_id):
            return
        message = update.effective_message
        if message is None or message.text is None:
            return

        try:
            target = normalize_youtube_url(message.text)
        except InvalidYouTubeUrl:
            await message.reply_text(
                "請傳送一個 HTTPS YouTube 單一影片連結（watch、youtu.be 或 Shorts）。"
            )
            return

        status = await message.reply_text("正在讀取影片資訊…")
        try:
            video = await self.downloader.probe(target)
        except UnsupportedMediaError as exc:
            await status.edit_text(str(exc))
            return
        except MetadataError:
            await status.edit_text(
                "無法讀取這段影片。它可能不可用、受限制，或 YouTube 暫時拒絕請求。"
            )
            return

        job = self.pending.put(video)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        choice.button_label,
                        callback_data=f"dl:{job.token}:{choice.value}",
                    )
                    for choice in DownloadChoice
                ]
            ]
        )
        await status.edit_text(
            f"{video.title}\n時長：{format_duration(video.duration_seconds)}\n\n請選擇格式：",
            reply_markup=keyboard,
        )
        LOGGER.info("Prepared choices for video %s", video.video_id)

    async def handle_choice(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        del context
        query = update.callback_query
        if query is None:
            return

        authorized = is_authorized(update, self.settings.allowed_user_id)
        if not authorized:
            await self._answer_callback(query, "未授權。", show_alert=True)
            return

        match = _CALLBACK.fullmatch(query.data or "")
        if match is None:
            await self._answer_callback(
                query,
                "這個按鈕無效，請重新傳送影片連結。",
                show_alert=True,
            )
            return

        token, raw_choice = match.groups()
        job = self.pending.consume(token)
        if job is None:
            await self._answer_callback(
                query,
                "這個選擇已過期或已使用，請重新傳送影片連結。",
                show_alert=True,
            )
            return
        await self._answer_callback(query)
        choice = DownloadChoice(raw_choice)

        await self._edit_callback_message(
            query, f"正在下載：{job.video.title}\n格式：{choice.button_label}"
        )
        try:
            async with self.downloader.download(job.video, choice) as media:
                message = query.message
                if message is None:
                    raise DownloadError("Callback message is unavailable")
                filename = safe_filename(job.video.title, media.path.suffix.lower())
                try:
                    with media.path.open("rb") as stream:
                        upload = InputFile(
                            stream,
                            filename=filename,
                            read_file_handle=False,
                        )
                        if choice is DownloadChoice.AUDIO_M4A:
                            await message.reply_audio(
                                audio=upload,
                                title=job.video.title[:64],
                                duration=job.video.duration_seconds,
                            )
                        else:
                            await message.reply_video(
                                video=upload,
                                caption=job.video.title[:1_000],
                                duration=job.video.duration_seconds,
                                supports_streaming=True,
                            )
                except OSError as exc:
                    raise DownloadError("Completed media could not be opened") from exc
        except FileTooLargeError:
            limit_mb = self.settings.max_file_bytes / 1_000_000
            await self._edit_callback_message(
                query,
                f"成品超過目前 {limit_mb:g} MB 的上傳上限，請改選較低畫質或 M4A。",
            )
        except DownloadTimeoutError:
            await self._edit_callback_message(query, "下載逾時，請稍後再試。")
        except DownloadError:
            await self._edit_callback_message(
                query, "下載失敗。影片可能受限制，或目前沒有可用的所選格式。"
            )
        except TelegramError as exc:
            LOGGER.warning("Telegram upload failed (%s)", type(exc).__name__)
            await self._edit_callback_message(
                query, "下載已完成，但 Telegram 上傳失敗，請稍後再試。"
            )
        else:
            LOGGER.info("Uploaded video %s as %s", job.video.video_id, choice.value)
            await self._edit_callback_message(query, "完成。你可以再傳送另一個連結。")

    @staticmethod
    async def _answer_callback(
        query: object,
        text: str | None = None,
        *,
        show_alert: bool = False,
    ) -> None:
        try:
            await query.answer(text=text, show_alert=show_alert)
        except TelegramError:
            LOGGER.info("Could not acknowledge a callback query")

    @staticmethod
    async def _edit_callback_message(query: object, text: str) -> None:
        try:
            await query.edit_message_text(text)
        except TelegramError:
            LOGGER.info("Could not update a callback status message")

    async def handle_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        del update
        error_name = type(context.error).__name__ if context.error else "UnknownError"
        LOGGER.error("Unhandled update error (%s)", error_name)
