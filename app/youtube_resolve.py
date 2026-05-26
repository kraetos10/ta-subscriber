"""Resolve a user-entered string (URL, handle, channel ID, video URL) to a UC channel ID."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger(__name__)

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
EXT_CHANNEL_ID_RE = re.compile(
    r'(?:"channelId"|"externalChannelId"|"externalId")\s*:\s*"(UC[A-Za-z0-9_-]{22})"'
)
META_CHANNEL_URL_RE = re.compile(
    r'<meta[^>]+itemprop="channelId"[^>]+content="(UC[A-Za-z0-9_-]{22})"'
)
CANONICAL_RE = re.compile(
    r'<link[^>]+rel="canonical"[^>]+href="[^"]*?/channel/(UC[A-Za-z0-9_-]{22})'
)


@dataclass(frozen=True)
class ResolvedChannel:
    channel_id: str
    name: str | None
    handle: str | None


class ResolveError(Exception):
    pass


class ChannelResolver:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns = client is None
        self.client = client or httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; ta-subscriber/1.0; "
                    "+https://github.com/)"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            cookies={"CONSENT": "YES+1", "SOCS": "CAI"},
        )

    def close(self) -> None:
        if self._owns:
            self.client.close()

    def resolve(self, raw: str) -> ResolvedChannel:
        value = (raw or "").strip()
        if not value:
            raise ResolveError("empty input")

        if CHANNEL_ID_RE.match(value):
            return self._fetch_channel_page(f"https://www.youtube.com/channel/{value}", fallback_id=value)

        url = self._normalise_url(value)
        return self._fetch_channel_page(url)

    def _normalise_url(self, value: str) -> str:
        if value.startswith("@"):
            return f"https://www.youtube.com/{value}"
        if re.fullmatch(r"[A-Za-z0-9_.\-]+", value):
            return f"https://www.youtube.com/@{value}"
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"https://www.youtube.com/{value.lstrip('/')}"

    def _fetch_channel_page(
        self, url: str, fallback_id: str | None = None
    ) -> ResolvedChannel:
        parsed = urlparse(url)
        if parsed.netloc and "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
            raise ResolveError(f"not a YouTube URL: {url}")

        is_video = False
        if "/watch" in parsed.path:
            qs = parse_qs(parsed.query)
            vid = (qs.get("v") or [None])[0]
            if vid:
                url = f"https://www.youtube.com/watch?v={vid}"
                is_video = True
        elif parsed.netloc.endswith("youtu.be"):
            vid = parsed.path.strip("/").split("/", 1)[0]
            if vid:
                url = f"https://www.youtube.com/watch?v={vid}"
                is_video = True

        body = self._fetch(url, fallback_id)
        if body is None:
            return ResolvedChannel(channel_id=fallback_id, name=None, handle=None)  # type: ignore[arg-type]

        channel_id = self._extract_channel_id(body) or fallback_id
        if not channel_id:
            raise ResolveError("could not find channel ID on the page")

        if is_video:
            channel_url = f"https://www.youtube.com/channel/{channel_id}"
            channel_body = self._fetch(channel_url, fallback_id=None)
            if channel_body:
                body = channel_body

        return ResolvedChannel(
            channel_id=channel_id,
            name=self._extract_name(body),
            handle=self._extract_handle(body),
        )

    def _fetch(self, url: str, fallback_id: str | None) -> str | None:
        try:
            r = self.client.get(url)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            if fallback_id is not None:
                return None
            raise ResolveError(f"fetch failed: {exc}") from exc
        return r.text

    @staticmethod
    def _extract_channel_id(body: str) -> str | None:
        for pattern in (CANONICAL_RE, META_CHANNEL_URL_RE, EXT_CHANNEL_ID_RE):
            m = pattern.search(body)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _extract_name(body: str) -> str | None:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', body)
        if m:
            return _html_decode(m.group(1))
        m = re.search(r'"author":"([^"]+)"', body)
        if m:
            return _html_decode(m.group(1))
        return None

    @staticmethod
    def _extract_handle(body: str) -> str | None:
        m = re.search(r'"canonicalBaseUrl":"/(@[A-Za-z0-9_.\-]+)"', body)
        if m:
            return m.group(1)
        return None


def _html_decode(value: str) -> str:
    import html

    return html.unescape(value).encode("utf-8").decode("unicode_escape", errors="ignore")
