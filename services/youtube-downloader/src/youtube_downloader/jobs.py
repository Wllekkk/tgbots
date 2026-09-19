from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from youtube_downloader.models import VideoInfo


@dataclass(frozen=True, slots=True)
class PendingJob:
    token: str
    video: VideoInfo
    expires_at: float


class PendingJobStore:
    """One in-memory pending choice, suitable for one sequential personal bot."""

    def __init__(
        self,
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(8),
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._token_factory = token_factory
        self._pending: PendingJob | None = None

    def put(self, video: VideoInfo) -> PendingJob:
        job = PendingJob(
            token=self._token_factory(),
            video=video,
            expires_at=self._clock() + self._ttl_seconds,
        )
        self._pending = job
        return job

    def consume(self, token: str) -> PendingJob | None:
        job = self._pending
        if job is None:
            return None
        if self._clock() >= job.expires_at:
            self._pending = None
            return None
        if not secrets.compare_digest(job.token, token):
            return None
        self._pending = None
        return job
