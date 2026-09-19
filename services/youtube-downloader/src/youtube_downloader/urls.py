from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_MAX_URL_LENGTH = 2_048


class InvalidYouTubeUrl(ValueError):
    """Raised when a message is not a supported single-video YouTube URL."""


@dataclass(frozen=True, slots=True)
class YouTubeUrl:
    canonical: str
    video_id: str


def normalize_youtube_url(raw: str) -> YouTubeUrl:
    value = raw.strip()
    if not value or len(value) > _MAX_URL_LENGTH:
        raise InvalidYouTubeUrl("URL is empty or too long")

    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise InvalidYouTubeUrl("URL could not be parsed") from exc

    hostname = (parts.hostname or "").lower()
    if (
        parts.scheme.lower() != "https"
        or hostname not in _ALLOWED_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        raise InvalidYouTubeUrl("URL origin is not allowed")

    if hostname == "youtu.be":
        segments = [segment for segment in parts.path.split("/") if segment]
        video_id = segments[0] if len(segments) == 1 else ""
    else:
        normalized_path = parts.path.rstrip("/") or "/"
        if normalized_path == "/watch":
            values = parse_qs(parts.query, keep_blank_values=True).get("v", [])
            video_id = values[0] if len(values) == 1 else ""
        else:
            segments = [segment for segment in parts.path.split("/") if segment]
            if len(segments) == 2 and segments[0] in {"shorts", "embed"}:
                video_id = segments[1]
            else:
                video_id = ""

    if not _VIDEO_ID.fullmatch(video_id):
        raise InvalidYouTubeUrl("URL does not identify one supported video")

    return YouTubeUrl(
        canonical=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
    )
