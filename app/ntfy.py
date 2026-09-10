"""ntfy push notifications for newly added videos.

Publishes via ntfy's JSON API: a POST of a single JSON object to the server
root (https://docs.ntfy.sh/publish/#publish-as-json), which keeps topic,
title, click URL and action buttons in one body rather than spread across
HTTP headers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

log = logging.getLogger(__name__)

DEFAULT_SERVER = "https://ntfy.sh"


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def normalise(server_url: str, topic: str) -> tuple[str, str]:
    """Return (publish endpoint, topic) from user-entered values.

    ntfy's own UI hands out topic URLs like `https://ntfy.sh/my-topic`, so
    accept one pasted into the server field: any path on the server URL is
    treated as the topic when the topic field is empty.
    """
    parts = urlsplit((server_url or DEFAULT_SERVER).strip())
    if not parts.netloc:  # bare host, e.g. "ntfy.example.com"
        parts = urlsplit(f"https://{parts.path.strip('/')}")
    path = parts.path.strip("/")
    topic = topic.strip() or path
    endpoint = urlunsplit((parts.scheme or "https", parts.netloc, "/", "", ""))
    return endpoint, topic


def build_message(
    *,
    title: str,
    channel_name: str | None,
    video_id: str,
    topic: str,
    ta_url: str | None,
    published: datetime | None,
    download_url: str | None = None,
) -> dict[str, object]:
    """Build the ntfy JSON publish payload for a new video."""
    meta: list[str] = []
    if channel_name:
        meta.append(channel_name)
    if published is not None:
        meta.append(published.date().isoformat())

    watch = youtube_url(video_id)
    ta_video = f"{ta_url.rstrip('/')}/video/{video_id}/" if ta_url else None
    actions: list[dict[str, str]] = [
        {"action": "view", "label": "YouTube", "url": watch}
    ]
    if ta_video:
        actions.append(
            {"action": "view", "label": "TubeArchivist", "url": ta_video}
        )
    if download_url:
        # Fires the request in the background from the phone; the link is
        # signed and scoped to this one video.
        actions.append(
            {
                "action": "http",
                "label": "Download",
                "url": download_url,
                "method": "POST",
                "clear": True,
            }
        )

    return {
        "topic": topic,
        "title": title,
        "message": " · ".join(meta) if meta else "New video added to queue",
        "tags": ["tv"],
        # Tapping the notification goes to the archived copy; the YouTube
        # action button remains for anything TA has not finished pulling.
        "click": ta_video or watch,
        "attach": thumbnail_url(video_id),
        "actions": actions,
    }


def post_new_video(
    server_url: str,
    topic: str,
    *,
    title: str,
    channel_name: str | None,
    video_id: str,
    token: str = "",
    ta_url: str | None = None,
    published: datetime | None = None,
    download_url: str | None = None,
    timeout: float = 10.0,
) -> bool:
    """Publish a new-video notification to an ntfy topic.

    Returns True on success. Never raises — logs and returns False so a
    notification failure can never break a scan.
    """
    endpoint, topic = normalise(server_url, topic)
    if not topic:
        return False
    payload = build_message(
        title=title,
        channel_name=channel_name,
        video_id=video_id,
        topic=topic,
        ta_url=ta_url,
        published=published,
        download_url=download_url,
    )
    headers = {"Authorization": f"Bearer {token.strip()}"} if token.strip() else {}
    try:
        r = httpx.post(endpoint, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        log.warning("ntfy notify failed for %s: %s", video_id, exc)
        return False
    if r.status_code >= 400:
        log.warning(
            "ntfy notify failed for %s (%s): %s",
            video_id,
            r.status_code,
            r.text[:200],
        )
        return False
    return True
