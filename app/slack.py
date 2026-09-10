"""Slack incoming-webhook notifications for newly added videos."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

log = logging.getLogger(__name__)


def _escape(text: str) -> str:
    """Escape the three characters Slack mrkdwn treats specially."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def build_message(
    *,
    title: str,
    channel_name: str | None,
    video_id: str,
    ta_url: str | None,
    published: datetime | None,
) -> dict[str, object]:
    """Build the Slack incoming-webhook payload for a new video.

    The bare YouTube URL is placed on its own final line: Slack unfurls a
    bare URL (not a labelled `<url|text>` link) into its native rich video
    preview — thumbnail, title and channel — which is the whole point.
    """
    lines = [":tv: *New video added to queue*", f"*{_escape(title)}*"]
    meta: list[str] = []
    if channel_name:
        meta.append(_escape(channel_name))
    if published is not None:
        meta.append(published.date().isoformat())
    if meta:
        lines.append(" · ".join(meta))
    if ta_url:
        lines.append(
            f"<{ta_url.rstrip('/')}/video/{video_id}/|Open in TubeArchivist>"
        )
    lines.append(youtube_url(video_id))
    return {"text": "\n".join(lines)}


def post_new_video(
    webhook_url: str,
    *,
    title: str,
    channel_name: str | None,
    video_id: str,
    ta_url: str | None = None,
    published: datetime | None = None,
    timeout: float = 10.0,
) -> bool:
    """Post a new-video notification to a Slack incoming webhook.

    Returns True on success. Never raises — logs and returns False so a
    notification failure can never break a scan.
    """
    if not webhook_url:
        return False
    payload = build_message(
        title=title,
        channel_name=channel_name,
        video_id=video_id,
        ta_url=ta_url,
        published=published,
    )
    ok, error = _post(webhook_url, payload, timeout)
    if not ok:
        log.warning("slack notify failed for %s: %s", video_id, error)
    return ok


def _post(
    webhook_url: str,
    payload: dict[str, object],
    timeout: float,
) -> tuple[bool, str | None]:
    """POST one message. Returns (ok, error) — never raises."""
    try:
        r = httpx.post(webhook_url, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, str(exc)
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    return True, None


def post_test(webhook_url: str, timeout: float = 10.0) -> tuple[bool, str | None]:
    """Send a test message. Returns (ok, error) for display in the UI."""
    if not webhook_url:
        return False, "no Slack webhook URL set"
    payload = {
        "text": ":white_check_mark: *ta-subscriber* — test notification. "
        "Slack is configured correctly."
    }
    return _post(webhook_url, payload, timeout)
