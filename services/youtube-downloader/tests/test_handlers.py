import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from telegram.constants import ChatType
from telegram.error import TelegramError

from youtube_downloader.config import Settings
from youtube_downloader.handlers import BotHandlers, is_authorized, safe_filename
from youtube_downloader.models import DownloadChoice, DownloadedMedia, VideoInfo


def update_for(user_id: int, chat_type: str = ChatType.PRIVATE):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=chat_type),
    )


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "BOT_TOKEN": "123:test",
            "ALLOWED_TELEGRAM_USER_ID": "42",
            "DOWNLOAD_DIR": str(tmp_path / "downloads"),
        }
    )


def test_authorization_requires_exact_user_and_private_chat() -> None:
    assert is_authorized(update_for(42), 42)
    assert not is_authorized(update_for(43), 42)
    assert not is_authorized(update_for(42, ChatType.GROUP), 42)
    assert not is_authorized(
        SimpleNamespace(effective_user=None, effective_chat=None), 42
    )


def test_safe_filename_removes_paths_and_control_characters() -> None:
    result = safe_filename(" ../a\\b\x00\n  測試  ", ".m4a")

    assert result == "a b 測試.m4a"
    assert "/" not in result
    assert "\\" not in result


def test_unauthorized_message_never_calls_downloader(tmp_path: Path) -> None:
    class Downloader:
        async def probe(self, target):
            raise AssertionError("unauthorized update reached downloader")

    class Message:
        text = "https://youtu.be/dQw4w9WgXcQ"

        async def reply_text(self, text):
            raise AssertionError("unauthorized update received a reply")

    update = update_for(43)
    update.effective_message = Message()
    handlers = BotHandlers(settings(tmp_path), Downloader())

    asyncio.run(handlers.handle_url(update, None))


def test_unauthorized_callback_is_answered_but_not_processed(tmp_path: Path) -> None:
    events = []

    class Downloader:
        def download(self, video, choice):
            raise AssertionError("unauthorized callback reached downloader")

    class Query:
        data = "dl:valid_token:audio"

        async def answer(self, **kwargs):
            events.append(kwargs)

    update = update_for(43)
    update.callback_query = Query()
    handlers = BotHandlers(settings(tmp_path), Downloader())

    asyncio.run(handlers.handle_choice(update, None))

    assert events == [{"text": "未授權。", "show_alert": True}]


def test_reused_callback_does_not_overwrite_completed_status(tmp_path: Path) -> None:
    events = []

    class Downloader:
        def download(self, video, choice):
            raise AssertionError("reused callback reached downloader")

    class Query:
        data = ""

        async def answer(self, **kwargs):
            events.append(kwargs)

        async def edit_message_text(self, text):
            raise AssertionError("reused callback changed the existing status")

    handlers = BotHandlers(settings(tmp_path), Downloader())
    job = handlers.pending.put(
        VideoInfo(
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            title="Example",
            duration_seconds=60,
        )
    )
    assert handlers.pending.consume(job.token) is job

    query = Query()
    query.data = f"dl:{job.token}:audio"
    update = update_for(42)
    update.callback_query = query

    asyncio.run(handlers.handle_choice(update, None))

    assert events == [
        {
            "text": "這個選擇已過期或已使用，請重新傳送影片連結。",
            "show_alert": True,
        }
    ]


def test_status_edit_failure_does_not_cancel_upload(tmp_path: Path) -> None:
    media_path = tmp_path / "result.m4a"
    media_path.write_bytes(b"audio")
    events = []

    class Downloader:
        @asynccontextmanager
        async def download(self, video, choice):
            events.append("download")
            try:
                yield DownloadedMedia(
                    path=media_path,
                    size_bytes=media_path.stat().st_size,
                    choice=choice,
                )
            finally:
                events.append("cleanup")
                media_path.unlink()

    class Message:
        async def reply_audio(self, *, audio, **kwargs):
            assert media_path.exists()
            assert not audio.input_file_content.closed
            events.append("upload")

    class Query:
        message = Message()
        data = ""

        async def answer(self, **kwargs):
            events.append("answer")

        async def edit_message_text(self, text):
            raise TelegramError("temporary status failure")

    handlers = BotHandlers(settings(tmp_path), Downloader())
    job = handlers.pending.put(
        VideoInfo(
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            title="Example",
            duration_seconds=60,
        )
    )
    query = Query()
    query.data = f"dl:{job.token}:{DownloadChoice.AUDIO_M4A.value}"
    update = update_for(42)
    update.callback_query = query

    asyncio.run(handlers.handle_choice(update, None))

    assert events == ["answer", "download", "upload", "cleanup"]
    assert not media_path.exists()
