# tgbots

A small monorepo for personal Telegram bots. Each bot lives in its own service
directory and can be deployed independently.

The first service accepts a single YouTube video URL and sends back one of:

- M4A audio
- MP4 video up to 360p
- MP4 video up to 720p

It is intentionally a personal, single-user bot: private chats only, one job at
a time, no database, and no public web port.

## Requirements

- A Linux VPS with Docker Engine and Docker Compose v2
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your numeric Telegram user ID

About 1 GB of RAM is recommended. The container is capped at 768 MB and one CPU;
a smaller VPS should have swap enabled. The container may restart if a
conversion exceeds its memory limit.

The container includes Python, `yt-dlp`, Deno, FFmpeg, and FFprobe. You do not
need to install those tools directly on the VPS.

## Configure

Clone the repository, then create the local environment file:

```bash
cp .env.example .env
chmod 600 .env
```

Edit these two required values in `.env`:

```dotenv
BOT_TOKEN=your-token-from-botfather
ALLOWED_TELEGRAM_USER_ID=your-numeric-user-id
```

To find your numeric ID without trusting a third-party bot:

1. Send any message to your newly created bot.
2. On your private VPS, run the following and enter the token when prompted.
3. Find `message.from.id` in the returned JSON.

```bash
read -rsp "Bot token: " TG_TOKEN; echo
printf 'url = "https://api.telegram.org/bot%s/getUpdates"\n' "$TG_TOKEN" \
  | curl -sS --config -
unset TG_TOKEN
```

Do not paste the token into issues, logs, screenshots, or chat messages. If it
is ever exposed, revoke it with BotFather and create a new one.

## Deploy

Build and start the bot:

```bash
docker compose up -d --build
docker compose logs -f youtube-downloader
```

Then open a private chat with the bot, send `/start`, and send a supported
YouTube URL. Choose the desired format from the inline buttons.

To update a deployment after pulling new commits:

```bash
git pull --ff-only
docker compose up -d --build
```

To stop it:

```bash
docker compose down
```

Downloaded media is temporary. A 256 MB `tmpfs` bounds the scratch space so an
unexpected stream cannot fill the VPS disk. It consumes RAM or swap only as it
is used; each job is deleted after upload and all data disappears with the
container. Increase the tmpfs and, if needed, the container memory limit only
when the VPS has enough RAM or swap.

## Current limits

- The standard Telegram Bot API accepts files up to 50 MB, so this service uses
  a conservative `49,000,000` byte limit and checks the completed file.
- Files are not recompressed to fit. If a result is too large, choose 360p or
  M4A instead.
- Playlists, active/upcoming live streams, sign-in, cookies, and custom
  `yt-dlp` options are not supported.
- The default maximum video duration is two hours.
- YouTube changes occasionally require a newer `yt-dlp` release. Update the
  pinned version in `services/youtube-downloader/requirements.txt`, test, and
  rebuild the image.

Configuration fields for a future self-hosted Telegram Local Bot API are
already reserved, but that extra service is not part of this minimal version.

## Security model

- Every message and callback checks the exact numeric user ID.
- Only private Telegram chats are accepted.
- Only exact HTTPS YouTube hostnames and supported single-video paths are
  accepted.
- The URL is reduced to a validated video ID and passed without a shell; users
  cannot inject arbitrary `yt-dlp` flags.
- The container runs as an unprivileged user with a read-only filesystem,
  dropped Linux capabilities, bounded memory/CPU/process counts, and no inbound
  ports.
- A bounded update queue applies backpressure while a long download is running.
- Dependency logs that can contain the Bot API URL are suppressed, and the bot
  token is redacted from final log output.
- The ignored `.env` file is injected into the container environment. Docker
  administrators can inspect it, so access to the VPS must remain trusted.

## Development

Run the offline test suite:

```bash
cd services/youtube-downloader
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest
```

Tests use fake command runners and do not download real videos.

## Adding another bot

Create another directory under `services/` and add a separate Compose service.
Keep dependencies and deployment boundaries per bot. Extract shared code only
after two services have a real, stable overlap.

## Responsible use

Download only media you are permitted to access and store. You are responsible
for complying with applicable law, copyright, the source site's terms, and
Telegram's terms.
