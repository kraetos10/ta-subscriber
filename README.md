# ta-subscriber

Standalone YouTube subscription monitor for [TubeArchivist](https://www.tubearchivist.com/). Polls YouTube channel RSS feeds on a schedule and pushes new videos to a TubeArchivist instance via its API. Deploys as a single Docker container with a built-in web UI.

This is a clean-room re-implementation of the closed-source subscription companion, using YouTube's public RSS feeds (no Google OAuth or API key required).

## What you get

- Web dashboard on port `8085` (host) → `8080` (container).
- API key + settings managed from the UI, persisted in SQLite.
- Add channels by pasting a URL, `@handle`, or `UC…` ID.
- Bulk import via OPML (YouTube Takeout export).
- One-click sync from channels you've already subscribed to inside TubeArchivist.
- Scheduled scan + "Scan now" button.
- Per-scan activity log and recent-queued list.
- Optional Slack and [ntfy](https://ntfy.sh) notifications for newly ingested videos.

## Quick start

```bash
cp .env.example .env             # optional; you can configure entirely via UI
docker compose up -d
open http://localhost:8085
```

### Using the prebuilt image

No clone needed. Save this as `docker-compose.yml` and run `docker compose up -d`:

```yaml
services:
  ta-subscriber:
    image: ghcr.io/kraetos10/ta-subscriber:latest
    container_name: ta-subscriber
    restart: unless-stopped
    ports:
      - "8085:8080"
    environment:
      TZ: UTC
    volumes:
      - ./data:/data
```

Configure everything else in the web UI. The optional environment variables are listed in `docker-compose.yml` in this repo.

On first launch you'll see "setup required". Open the UI and:

1. Paste your TubeArchivist URL (e.g. `http://tubearchivist:8000`) and API token (Settings → User → Access Token in TA). Click **Test connection**.
2. Add channels under **Channels**: paste a URL like `https://www.youtube.com/@mkbhd`, a handle like `@mkbhd`, or a `UC…` channel ID. Use **Import OPML** for bulk import, or **Sync from TA subscriptions** to pull whatever you've already subscribed to inside TubeArchivist.
3. Adjust scan interval, max age, and autostart as needed. Click **Save**.
4. Hit **Scan now** to run an immediate scan.

## How it works

1. Loads the channel list from the source you pick (default: the channels you add via the UI, stored in SQLite).
2. Every `scan_interval_minutes`, GETs `https://www.youtube.com/feeds/videos.xml?channel_id=…` for each enabled channel.
3. Filters to entries published in the last `max_age_days` days (avoids backfilling huge channels on first run).
4. Diffs against the local "seen" table.
5. POSTs new video IDs to `<ta_url>/api/download/` with your token. With `autostart=true`, TA starts downloading immediately; otherwise they sit in the queue.

## Adding a channel

The "Add channel" input accepts any of:

| Input | Example |
| --- | --- |
| Channel URL with ID | `https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ` |
| Handle URL | `https://www.youtube.com/@mkbhd` |
| Handle alone | `@mkbhd` or `mkbhd` |
| Legacy `/c/` or `/user/` URL | `https://www.youtube.com/c/SomeChannel` |
| Video URL | `https://www.youtube.com/watch?v=…` (resolves to that video's channel) |
| Bare channel ID | `UCBJycsmduvYEL83R_U4JriQ` |

The resolver fetches the YouTube page and extracts the canonical `UC…` ID. No Google API key required.

## Notifications

Both are optional, off by default, and configured in the UI (or seeded from `.env` on first run). A notification is sent once per video, only after TubeArchivist confirms it ingested it.

**Send test notification** in the UI fires a test message to every channel currently filled in on the form — before saving — and reports each one separately, so a bad webhook or an unreachable ntfy server shows up immediately.

- **Slack** — paste an [incoming webhook](https://api.slack.com/messaging/webhooks) URL. The message ends with a bare YouTube URL so Slack unfurls its native video preview.
- **ntfy** — set a topic (and a server, if you self-host; default `https://ntfy.sh`). Published as [JSON](https://docs.ntfy.sh/publish/#publish-as-json) with the video title as the notification title, the channel and publish date as the body, the thumbnail attached, and **YouTube** / **TubeArchivist** action buttons. Tapping the notification opens the archived copy in TubeArchivist (or YouTube, if no TA URL is set).

  If you set **Public URL** (the address this app answers on from your phone), notifications gain a third button, **Download**, which pulls that one video into TubeArchivist immediately — useful when `autostart` is off and the video is sitting in the queue. The button posts to this app, which holds the TA token; the link carries an HMAC covering that single video ID, so a leaked notification can trigger that download and nothing else. The signing secret is generated on first run, stored in the DB, and never returned by the API.

  Topic names are public on `ntfy.sh` — anyone who guesses yours can read it — so use an unguessable name, or an [access-protected](https://docs.ntfy.sh/config/#access-control) topic with a token. `NTFY_TOKEN` is sent as `Authorization: Bearer …`.

## Channel sources

The **source** dropdown decides where the channel list comes from at scan time:

- **db** (default, recommended) — uses the channels you manage in the UI.
- **opml** — reads `/data/subscriptions.opml` from the mounted volume.
- **file** — reads `/data/channels.txt`, one channel ID per line, `#` for comments.
- **env** — reads `CHANNEL_IDS` env var, comma-separated.
- **ta** — pulls subscribed channels from your TubeArchivist instance at every scan. Note that TA already scans those itself; choose this only if you want a faster RSS heartbeat than TA's built-in scan.

## Configuration

All values are persisted in `./data/subscriber.db`. Environment variables in `.env` are only consulted on first run to seed the DB. To change a setting after that, use the UI.

| Setting | Default | Notes |
| --- | --- | --- |
| `TA_URL` | — | TubeArchivist base URL, no trailing slash |
| `TA_TOKEN` | — | TA API token |
| `SCAN_INTERVAL_MINUTES` | `60` | Range 5–10080 |
| `AUTOSTART` | `false` | Auto-start downloads on queue |
| `MAX_AGE_DAYS` | `7` | Ignore RSS entries older than this |
| `SOURCE` | `db` | See above |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook; blank disables |
| `NTFY_URL` | `https://ntfy.sh` | ntfy server base URL |
| `NTFY_TOPIC` | — | ntfy topic; blank disables |
| `NTFY_TOKEN` | — | ntfy access token, for protected topics |
| `PUBLIC_URL` | — | Public address of this app; enables the ntfy Download button |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `TZ` | `UTC` | Container timezone |

## Joining the TubeArchivist network

If TA runs in its own compose stack, attach this container to that network so it can reach `http://tubearchivist:8000`:

```yaml
services:
  ta-subscriber:
    # ...
    networks:
      - tubearchivist_default
networks:
  tubearchivist_default:
    external: true
```

## Operational notes

- **State**: `./data/subscriber.db` (SQLite). Backup if you want, but losing it just means the next scan re-queues recent videos that TA may already have — TA dedupes by video ID.
- **Rate**: RSS feeds are free and unmetered, but keep `scan_interval_minutes` ≥ 15.
- **Quota**: No YouTube Data API key needed. Only public endpoints are hit.
- **RSS limit**: YouTube RSS returns the latest 15 videos per channel. Default interval (60m) + `max_age_days=7` makes missed uploads very unlikely.
- **Auth**: No auth on the web UI itself — put it behind your usual reverse proxy / VPN. Don't expose 8085 to the public internet.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export STATE_DIR=./data
export PORT=8080
python -m app.main
```

## Licence

MIT, see [LICENSE](LICENSE).
