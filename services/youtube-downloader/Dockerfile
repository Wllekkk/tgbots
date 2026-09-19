ARG DENO_VERSION=2.9.6
FROM denoland/deno:bin-${DENO_VERSION} AS deno

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    DENO_NO_UPDATE_CHECK=1 \
    DENO_NO_PROMPT=1 \
    DENO_DIR=/tmp/deno \
    XDG_CACHE_HOME=/tmp/cache

COPY --from=deno /deno /usr/local/bin/deno

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid bot --create-home \
       --home-dir /home/bot --shell /usr/sbin/nologin bot \
    && install -d -o bot -g bot /app /data/downloads

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=10001:10001 src/ ./src/

RUN deno --version \
    && ffmpeg -version >/dev/null \
    && ffprobe -version >/dev/null \
    && python -c "import telegram, yt_dlp; print(telegram.__version__, yt_dlp.version.__version__)"

USER 10001:10001

CMD ["python", "-m", "youtube_downloader"]
