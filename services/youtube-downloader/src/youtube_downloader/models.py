from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DownloadChoice(StrEnum):
    AUDIO_M4A = "audio"
    VIDEO_360 = "video360"
    VIDEO_720 = "video720"

    @property
    def button_label(self) -> str:
        return {
            self.AUDIO_M4A: "音頻 · M4A",
            self.VIDEO_360: "視頻 · ≤360p",
            self.VIDEO_720: "視頻 · ≤720p",
        }[self]

    @property
    def extension(self) -> str:
        return ".m4a" if self is self.AUDIO_M4A else ".mp4"

    @property
    def height(self) -> int | None:
        return {
            self.AUDIO_M4A: None,
            self.VIDEO_360: 360,
            self.VIDEO_720: 720,
        }[self]


@dataclass(frozen=True, slots=True)
class VideoInfo:
    source_url: str
    video_id: str
    title: str
    duration_seconds: int


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    path: Path
    size_bytes: int
    choice: DownloadChoice


class MetadataError(RuntimeError):
    """Metadata could not be obtained safely."""


class UnsupportedMediaError(MetadataError):
    """The target is valid but outside the MVP's supported media types."""


class DownloadError(RuntimeError):
    """The selected media could not be downloaded."""


class DownloadTimeoutError(DownloadError):
    """The download exceeded its configured deadline."""


class FileTooLargeError(DownloadError):
    """The completed file exceeds the configured upload size."""
