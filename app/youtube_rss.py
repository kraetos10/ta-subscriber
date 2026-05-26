"""Fetch and parse YouTube channel RSS feeds."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import httpx

log = logging.getLogger(__name__)

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


def classify_video(client: httpx.Client, video_id: str) -> str:
    """Return 'shorts', 'stream', or 'video'. Uses one HEAD-style GET to /shorts/<id>."""
    try:
        r = client.get(
            f"https://www.youtube.com/shorts/{video_id}",
            follow_redirects=False,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        log.debug("classify failed for %s: %s", video_id, exc)
        return "video"

    if r.status_code in (301, 302, 303, 307, 308):
        target = r.headers.get("location", "")
        if "/watch" in target:
            return _check_stream(client, video_id)
        return "video"
    if r.status_code == 200:
        if b"shorts" in r.content[:8000].lower():
            return "shorts"
        return "video"
    return "video"


_STREAM_RE = re.compile(rb'"isLiveContent"\s*:\s*true|"liveBroadcastDetails"')


def _check_stream(client: httpx.Client, video_id: str) -> str:
    try:
        r = client.get(
            f"https://www.youtube.com/watch?v={video_id}",
            follow_redirects=True,
            timeout=10.0,
        )
    except httpx.HTTPError:
        return "video"
    if r.status_code == 200 and _STREAM_RE.search(r.content):
        return "stream"
    return "video"


@dataclass(frozen=True)
class VideoEntry:
    video_id: str
    channel_id: str
    title: str
    published: datetime


class RSSFetcher:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def fetch(self, channel_id: str) -> list[VideoEntry]:
        url = RSS_URL.format(cid=channel_id)
        try:
            r = self.client.get(url, timeout=20.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("RSS fetch failed for %s: %s", channel_id, exc)
            return []

        parsed = feedparser.parse(r.content)
        entries: list[VideoEntry] = []
        for entry in parsed.entries:
            vid = entry.get("yt_videoid") or _extract_id(entry.get("id", ""))
            if not vid:
                continue
            published = _parse_dt(entry.get("published"))
            if published is None:
                continue
            entries.append(
                VideoEntry(
                    video_id=vid,
                    channel_id=channel_id,
                    title=entry.get("title", ""),
                    published=published,
                )
            )
        return entries


def _extract_id(rss_id: str) -> str | None:
    if "video:" in rss_id:
        return rss_id.split("video:", 1)[1]
    return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
