import asyncio
import json
import math
import os
import signal
from pathlib import Path

import pytest

import youtube_downloader.ytdlp as ytdlp_module
from youtube_downloader.models import (
    DownloadChoice,
    DownloadError,
    FileTooLargeError,
    MetadataError,
    UnsupportedMediaError,
    VideoInfo,
)
from youtube_downloader.urls import YouTubeUrl
from youtube_downloader.ytdlp import (
    CommandResult,
    CommandTimedOut,
    YtDlpService,
    run_command,
)

TARGET = YouTubeUrl(
    canonical="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    video_id="dQw4w9WgXcQ",
)
VIDEO = VideoInfo(
    source_url=TARGET.canonical,
    video_id=TARGET.video_id,
    title="Example",
    duration_seconds=60,
)


class HangingProcess:
    def __init__(self, *, returncode=None) -> None:
        self.pid = 12345
        self.returncode = returncode
        self.started = asyncio.Event()

    async def communicate(self):
        self.started.set()
        await asyncio.Future()

    async def wait(self):
        if self.returncode is None:
            self.returncode = -signal.SIGTERM
        return self.returncode


def make_service(tmp_path: Path, runner, *, max_file_bytes: int = 49_000_000):
    return YtDlpService(
        download_dir=tmp_path / "downloads",
        max_file_bytes=max_file_bytes,
        max_duration_seconds=7_200,
        metadata_timeout_seconds=45,
        download_timeout_seconds=900,
        runner=runner,
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_run_command_terminates_process_group_on_timeout(monkeypatch) -> None:
    process = HangingProcess()
    signals = []

    async def create_process(*argv, **kwargs):
        assert kwargs["start_new_session"] is True
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(
        "youtube_downloader.ytdlp.os.killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    with pytest.raises(CommandTimedOut):
        asyncio.run(run_command(["yt-dlp", "url"], timeout_seconds=0.01))

    assert signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_run_command_terminates_process_group_when_cancelled(monkeypatch) -> None:
    process = HangingProcess()
    signals = []

    async def create_process(*argv, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(
        "youtube_downloader.ytdlp.os.killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    async def exercise():
        task = asyncio.create_task(run_command(["yt-dlp", "url"], 60))
        await process.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_process_group_is_signalled_when_leader_already_exited(monkeypatch) -> None:
    process = HangingProcess(returncode=0)
    signals = []

    monkeypatch.setattr(
        "youtube_downloader.ytdlp.os.killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    asyncio.run(ytdlp_module._terminate_process_group(process))

    assert signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


def test_probe_returns_only_selected_metadata(tmp_path: Path) -> None:
    seen = []

    async def runner(argv, timeout):
        seen.append((list(argv), timeout))
        return CommandResult(
            0,
            json.dumps(
                {
                    "id": TARGET.video_id,
                    "title": " A title ",
                    "duration": 61.2,
                    "live_status": "not_live",
                }
            ).encode(),
            b"",
        )

    service = make_service(tmp_path, runner)
    result = asyncio.run(service.probe(TARGET))

    assert result.title == "A title"
    assert result.duration_seconds == 62
    assert "--ignore-config" in seen[0][0]
    assert "--no-playlist" in seen[0][0]
    assert seen[0][0][-1] == TARGET.canonical
    assert seen[0][1] == 45


@pytest.mark.parametrize(
    "metadata",
    [
        {"_type": "playlist", "entries": [{"id": TARGET.video_id}]},
        {"duration": 60, "is_live": True},
        {"duration": 60, "live_status": "is_upcoming"},
        {"duration": 60, "live_status": "post_live"},
        {"duration": None},
        {"duration": 7_201},
    ],
)
def test_probe_rejects_unsupported_media(tmp_path: Path, metadata: dict) -> None:
    async def runner(argv, timeout):
        payload = {"id": TARGET.video_id, **metadata}
        return CommandResult(0, json.dumps(payload).encode(), b"")

    service = make_service(tmp_path, runner)
    with pytest.raises(UnsupportedMediaError):
        asyncio.run(service.probe(TARGET))


@pytest.mark.parametrize("duration", [math.nan, math.inf, -math.inf])
def test_probe_rejects_non_finite_duration(tmp_path: Path, duration: float) -> None:
    async def runner(argv, timeout):
        return CommandResult(
            0,
            json.dumps({"id": TARGET.video_id, "duration": duration}).encode(),
            b"",
        )

    service = make_service(tmp_path, runner)
    with pytest.raises(UnsupportedMediaError):
        asyncio.run(service.probe(TARGET))


@pytest.mark.parametrize("reported_id", [None, "aaaaaaaaaaa"])
def test_probe_rejects_missing_or_mismatched_id(
    tmp_path: Path, reported_id: str | None
) -> None:
    async def runner(argv, timeout):
        return CommandResult(
            0,
            json.dumps({"id": reported_id, "duration": 60}).encode(),
            b"",
        )

    service = make_service(tmp_path, runner)
    with pytest.raises(MetadataError, match="unexpected video"):
        asyncio.run(service.probe(TARGET))


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (DownloadChoice.AUDIO_M4A, "bestaudio[ext=m4a]/bestaudio"),
        (DownloadChoice.VIDEO_360, "height<=360"),
        (DownloadChoice.VIDEO_720, "height<=720"),
    ],
)
def test_download_command_uses_fixed_presets(
    tmp_path: Path, choice: DownloadChoice, expected: str
) -> None:
    async def unused_runner(argv, timeout):
        raise AssertionError("runner should not be called")

    service = make_service(tmp_path, unused_runner)
    workspace = tmp_path / "job"
    command = service.build_download_command(
        source_url=VIDEO.source_url,
        choice=choice,
        workspace=workspace,
        result_file=workspace / "result.txt",
    )
    selector = command[command.index("-f") + 1]

    assert expected in selector
    assert "--ignore-config" in command
    assert "--no-playlist" in command
    assert command[-1] == VIDEO.source_url
    if choice.height is not None:
        assert f"height<={choice.height}" in selector
        assert "height>" not in selector


def file_runner(size: int, extension: str = "m4a"):
    async def runner(argv, timeout):
        output_template = Path(argv[argv.index("-o") + 1])
        output = Path(str(output_template).replace("%(ext)s", extension))
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as stream:  # noqa: ASYNC230 - creates a sparse fixture
            stream.truncate(size)
        result_file = Path(argv[argv.index("--print-to-file") + 2])
        result_file.write_text(str(output), encoding="utf-8")
        return CommandResult(0, b"", b"")

    return runner


@pytest.mark.parametrize("size", [48_999_999, 49_000_000])
def test_completed_file_size_boundary_is_accepted(tmp_path: Path, size: int) -> None:
    service = make_service(tmp_path, file_runner(size))

    async def exercise():
        async with service.download(VIDEO, DownloadChoice.AUDIO_M4A) as media:
            assert media.size_bytes == size
            assert media.path.exists()
            workspace = media.path.parent
        assert not workspace.exists()

    asyncio.run(exercise())


def test_oversized_completed_file_is_rejected_and_cleaned(tmp_path: Path) -> None:
    service = make_service(tmp_path, file_runner(49_000_001))

    async def exercise():
        with pytest.raises(FileTooLargeError):
            async with service.download(VIDEO, DownloadChoice.AUDIO_M4A):
                raise AssertionError("oversized media must not be yielded")

    asyncio.run(exercise())
    assert list(service.download_dir.iterdir()) == []


def test_final_postprocessed_path_is_used(tmp_path: Path) -> None:
    service = make_service(tmp_path, file_runner(100, extension="mp4"))

    async def exercise():
        async with service.download(VIDEO, DownloadChoice.VIDEO_360) as media:
            assert media.path.name == "media.mp4"

    asyncio.run(exercise())


def test_path_outside_workspace_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.m4a"
    outside.write_bytes(b"content")

    async def runner(argv, timeout):
        result_file = Path(argv[argv.index("--print-to-file") + 2])
        result_file.write_text(str(outside), encoding="utf-8")
        return CommandResult(0, b"", b"")

    service = make_service(tmp_path, runner)

    async def exercise():
        with pytest.raises(DownloadError, match="escaped"):
            async with service.download(VIDEO, DownloadChoice.AUDIO_M4A):
                raise AssertionError("outside path must not be yielded")

    asyncio.run(exercise())
    assert outside.exists()
    assert list(service.download_dir.iterdir()) == []


def test_nonzero_exit_is_safe_and_cleans_workspace(tmp_path: Path) -> None:
    async def runner(argv, timeout):
        return CommandResult(1, b"", b"private upstream details")

    service = make_service(tmp_path, runner)

    async def exercise():
        with pytest.raises(DownloadError, match="yt-dlp failed") as captured:
            async with service.download(VIDEO, DownloadChoice.AUDIO_M4A):
                raise AssertionError("failed media must not be yielded")
        assert "private upstream details" not in str(captured.value)

    asyncio.run(exercise())
    assert list(service.download_dir.iterdir()) == []


def test_stale_job_cleanup_does_not_touch_other_files(tmp_path: Path) -> None:
    async def unused_runner(argv, timeout):
        raise AssertionError("runner should not be called")

    service = make_service(tmp_path, unused_runner)
    service.download_dir.mkdir()
    stale = service.download_dir / "job-stale"
    stale.mkdir()
    (stale / "media.part").write_bytes(b"partial")
    keep = service.download_dir / "keep.txt"
    keep.write_text("keep", encoding="utf-8")

    service.cleanup_stale_jobs()

    assert not stale.exists()
    assert keep.read_text(encoding="utf-8") == "keep"
