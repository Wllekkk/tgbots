from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import signal
import stat
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from youtube_downloader.models import (
    DownloadChoice,
    DownloadedMedia,
    DownloadError,
    DownloadTimeoutError,
    FileTooLargeError,
    MetadataError,
    UnsupportedMediaError,
    VideoInfo,
)
from youtube_downloader.urls import YouTubeUrl

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandTimedOut(TimeoutError):
    pass


CommandRunner = Callable[[Sequence[str], int], Awaitable[CommandResult]]


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    is_posix = os.name == "posix"
    if not is_posix and process.returncode is not None:
        return

    try:
        if is_posix:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return

    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass

    try:
        if is_posix:
            # The group leader may already have exited while FFmpeg or another
            # child still owns the pipes. Always signal the whole group again.
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        return
    if process.returncode is None:
        await process.wait()


async def run_command(argv: Sequence[str], timeout_seconds: int) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        await asyncio.shield(_terminate_process_group(process))
        raise CommandTimedOut from exc
    except asyncio.CancelledError:
        await asyncio.shield(_terminate_process_group(process))
        raise
    except Exception:
        await asyncio.shield(_terminate_process_group(process))
        raise
    return CommandResult(process.returncode or 0, stdout, stderr)


class YtDlpService:
    def __init__(
        self,
        *,
        download_dir: Path,
        max_file_bytes: int,
        max_duration_seconds: int,
        metadata_timeout_seconds: int,
        download_timeout_seconds: int,
        runner: CommandRunner = run_command,
        executable: str = "yt-dlp",
    ) -> None:
        self.download_dir = download_dir
        self.max_file_bytes = max_file_bytes
        self.max_duration_seconds = max_duration_seconds
        self.metadata_timeout_seconds = metadata_timeout_seconds
        self.download_timeout_seconds = download_timeout_seconds
        self._runner = runner
        self._executable = executable

    def cleanup_stale_jobs(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        for entry in self.download_dir.iterdir():
            if not entry.name.startswith("job-"):
                continue
            try:
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
            except OSError:
                LOGGER.warning("Could not remove a stale download workspace")

    async def probe(self, target: YouTubeUrl) -> VideoInfo:
        argv = [
            self._executable,
            "--ignore-config",
            "--no-playlist",
            "--skip-download",
            "--dump-single-json",
            "--no-warnings",
            "--socket-timeout",
            "15",
            target.canonical,
        ]
        try:
            result = await self._runner(argv, self.metadata_timeout_seconds)
        except CommandTimedOut as exc:
            raise MetadataError("Metadata request timed out") from exc
        except OSError as exc:
            raise MetadataError("yt-dlp could not be started") from exc

        if result.returncode != 0:
            LOGGER.warning(
                "Metadata lookup failed for video %s (exit %s)",
                target.video_id,
                result.returncode,
            )
            raise MetadataError("Metadata lookup failed")

        try:
            payload = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MetadataError("yt-dlp returned invalid metadata") from exc
        if not isinstance(payload, dict):
            raise MetadataError("yt-dlp returned invalid metadata")

        if payload.get("id") != target.video_id:
            raise MetadataError("yt-dlp returned metadata for an unexpected video")

        if payload.get("_type") in {"playlist", "multi_video"} or payload.get(
            "entries"
        ):
            raise UnsupportedMediaError("播放清單不在目前支援範圍內。")
        if payload.get("is_live") is True or payload.get("live_status") in {
            "is_live",
            "is_upcoming",
            "post_live",
        }:
            raise UnsupportedMediaError("直播或尚在處理的直播暫不支援。")

        duration = payload.get("duration")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
        ):
            raise UnsupportedMediaError("無法確認影片時長，暫不下載。")
        duration_seconds = math.ceil(duration)
        if duration_seconds <= 0:
            raise UnsupportedMediaError("無法確認影片時長，暫不下載。")
        if duration_seconds > self.max_duration_seconds:
            raise UnsupportedMediaError(
                f"影片超過目前 {self.max_duration_seconds // 60} 分鐘的時長限制。"
            )

        raw_title = payload.get("title")
        title = raw_title.strip() if isinstance(raw_title, str) else ""
        if not title:
            title = f"YouTube {target.video_id}"

        return VideoInfo(
            source_url=target.canonical,
            video_id=target.video_id,
            title=title[:200],
            duration_seconds=duration_seconds,
        )

    def build_download_command(
        self,
        *,
        source_url: str,
        choice: DownloadChoice,
        workspace: Path,
        result_file: Path,
    ) -> list[str]:
        argv = [
            self._executable,
            "--ignore-config",
            "--no-playlist",
            "--max-downloads",
            "1",
            "--no-progress",
            "--no-warnings",
            "--socket-timeout",
            "15",
            "--retries",
            "3",
            "--fragment-retries",
            "3",
            "--max-filesize",
            str(self.max_file_bytes),
            "-o",
            str(workspace / "media.%(ext)s"),
            "--print-to-file",
            "after_move:%(filepath)s",
            str(result_file),
        ]

        if choice is DownloadChoice.AUDIO_M4A:
            argv.extend(
                [
                    "-f",
                    "bestaudio[ext=m4a]/bestaudio",
                    "-x",
                    "--audio-format",
                    "m4a",
                    "--audio-quality",
                    "128K",
                ]
            )
        else:
            height = choice.height
            format_selector = (
                f"best[height<={height}][ext=mp4][vcodec^=avc1]/"
                f"bestvideo[height<={height}][ext=mp4][vcodec^=avc1]"
                "+bestaudio[ext=m4a]/"
                f"best[height<={height}][ext=mp4]/"
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]"
            )
            argv.extend(
                [
                    "-f",
                    format_selector,
                    "--merge-output-format",
                    "mp4",
                    "--remux-video",
                    "mp4",
                ]
            )

        argv.append(source_url)
        return argv

    @asynccontextmanager
    async def download(
        self, video: VideoInfo, choice: DownloadChoice
    ) -> AsyncIterator[DownloadedMedia]:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        workspace = Path(
            tempfile.mkdtemp(prefix="job-", dir=self.download_dir)
        ).resolve()

        try:
            result_file = workspace / "result.txt"
            argv = self.build_download_command(
                source_url=video.source_url,
                choice=choice,
                workspace=workspace,
                result_file=result_file,
            )
            try:
                result = await self._runner(argv, self.download_timeout_seconds)
            except CommandTimedOut as exc:
                raise DownloadTimeoutError("Download timed out") from exc
            except OSError as exc:
                raise DownloadError("yt-dlp could not be started") from exc

            if result.returncode != 0:
                LOGGER.warning(
                    "Download failed for video %s (exit %s)",
                    video.video_id,
                    result.returncode,
                )
                raise DownloadError("yt-dlp failed")

            path = self._read_completed_path(result_file, workspace)
            try:
                file_stat = path.stat()
            except OSError as exc:
                raise DownloadError("Completed media could not be inspected") from exc
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size <= 0:
                raise DownloadError("yt-dlp did not create a regular media file")
            if path.suffix.lower() != choice.extension:
                raise DownloadError("yt-dlp created an unexpected media format")
            if file_stat.st_size > self.max_file_bytes:
                raise FileTooLargeError("Completed media exceeds upload limit")

            yield DownloadedMedia(
                path=path,
                size_bytes=file_stat.st_size,
                choice=choice,
            )
        finally:
            try:
                shutil.rmtree(workspace)
            except OSError:
                LOGGER.warning("Could not remove a completed download workspace")

    @staticmethod
    def _read_completed_path(result_file: Path, workspace: Path) -> Path:
        try:
            lines = [
                line.strip()
                for line in result_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, UnicodeError) as exc:
            raise DownloadError("yt-dlp did not report the completed file") from exc
        if not lines:
            raise DownloadError("yt-dlp did not report the completed file")

        candidate = Path(lines[-1])
        if not candidate.is_absolute():
            candidate = workspace / candidate
        if candidate.is_symlink():
            raise DownloadError("Completed media path is a symlink")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(workspace)
        except (OSError, ValueError) as exc:
            raise DownloadError("Completed media path escaped its workspace") from exc
        return resolved
