from youtube_downloader.jobs import PendingJobStore
from youtube_downloader.models import VideoInfo


def video(identifier: str = "dQw4w9WgXcQ") -> VideoInfo:
    return VideoInfo(
        source_url=f"https://www.youtube.com/watch?v={identifier}",
        video_id=identifier,
        title="Example",
        duration_seconds=60,
    )


def test_pending_job_is_single_use() -> None:
    store = PendingJobStore(600, token_factory=lambda: "token_one")
    job = store.put(video())

    assert store.consume(job.token) == job
    assert store.consume(job.token) is None


def test_new_job_invalidates_old_buttons() -> None:
    tokens = iter(["old_token", "new_token"])
    store = PendingJobStore(600, token_factory=lambda: next(tokens))
    old = store.put(video())
    new = store.put(video("aaaaaaaaaaa"))

    assert store.consume(old.token) is None
    assert store.consume(new.token) == new


def test_wrong_token_does_not_consume_current_job() -> None:
    store = PendingJobStore(600, token_factory=lambda: "right_token")
    job = store.put(video())

    assert store.consume("wrong_token") is None
    assert store.consume(job.token) == job


def test_expiry_uses_monotonic_deadline() -> None:
    now = [100.0]
    store = PendingJobStore(
        10,
        clock=lambda: now[0],
        token_factory=lambda: "expires_now",
    )
    job = store.put(video())
    now[0] = 110.0

    assert store.consume(job.token) is None
