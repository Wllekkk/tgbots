import pytest

from youtube_downloader.urls import InvalidYouTubeUrl, normalize_youtube_url

VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize(
    "url",
    [
        f"https://youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123&t=10",
        f"https://m.youtube.com/watch?v={VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"https://www.youtube.com:443/watch?v={VIDEO_ID}",
    ],
)
def test_supported_urls_are_canonicalized(url: str) -> None:
    normalized = normalize_youtube_url(url)

    assert normalized.video_id == VIDEO_ID
    assert normalized.canonical == CANONICAL


@pytest.mark.parametrize(
    "url",
    [
        f"http://youtube.com/watch?v={VIDEO_ID}",
        f"ftp://youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com:444/watch?v={VIDEO_ID}",
        f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
        f"https://evilyoutube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com@evil.example/watch?v={VIDEO_ID}",
        f"https://user@youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/playlist?list={VIDEO_ID}",
        f"https://www.youtube.com/channel/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}/extra",
        "https://www.youtube.com/watch?v=too-short",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&v=aaaaaaaaaaa",
        "not a URL",
        "javascript:alert(1)",
        "",
    ],
)
def test_unsupported_or_deceptive_urls_are_rejected(url: str) -> None:
    with pytest.raises(InvalidYouTubeUrl):
        normalize_youtube_url(url)
