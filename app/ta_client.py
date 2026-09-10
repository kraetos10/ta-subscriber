"""TubeArchivist API client."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TAError(Exception):
    pass


class TAClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.verify = verify
        self.client = httpx.Client(
            timeout=timeout,
            verify=verify,
            headers={
                "Authorization": f"Token {token}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self.client.close()

    def ping(self) -> tuple[bool, str | None]:
        try:
            r = self.client.get(f"{self.base_url}/api/ping/")
        except httpx.HTTPError as exc:
            log.warning("ping failed: %s", exc)
            return False, str(exc)
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        return True, None

    def list_subscribed_channels(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            r = self.client.get(
                f"{self.base_url}/api/channel/",
                params={"filter": "subscribed", "page": page},
            )
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data", [])
            out.extend(data)
            paginate = payload.get("paginate") or {}
            last_page = paginate.get("last_page") or 1
            if page >= last_page or not data:
                break
            page += 1
        return out

    def is_ingested(self, video_id: str) -> bool:
        """True once TA has actually taken the video into its DB.

        TA's POST /api/download/ returns 200 immediately but extracts metadata
        in a background task; if extraction fails (e.g. a freshly-ended live
        stream whose VOD is still processing) the video is silently dropped and
        appears in no TA state. We treat presence in the video table OR the
        download queue as proof of ingestion.
        """
        for path in (f"/api/video/{video_id}/", f"/api/download/{video_id}/"):
            try:
                r = self.client.get(f"{self.base_url}{path}")
            except httpx.HTTPError as exc:
                log.debug("ingest check failed for %s (%s): %s", video_id, path, exc)
                continue
            if r.status_code == 200:
                return True
        return False

    def download_now(self, video_id: str) -> bool:
        """Make TA download one already-queued video now.

        POSTing status "priority" flips the queue item to auto_start and TA
        itself kicks off a download run for auto-start items, so this single
        call is enough to pull the video. Returns False if TA no longer has
        the video in its download queue (already downloaded, or forgotten).
        """
        r = self.client.post(
            f"{self.base_url}/api/download/{video_id}/",
            json={"status": "priority"},
        )
        if r.status_code == 404:
            return False
        if r.status_code >= 400:
            raise TAError(
                f"download_now failed ({r.status_code}): {r.text[:300]}"
            )
        log.info("triggered download of %s", video_id)
        return True

    def queue_videos(self, video_ids: list[str], autostart: bool) -> None:
        if not video_ids:
            return
        body = {
            "data": [{"youtube_id": vid, "status": "pending"} for vid in video_ids]
        }
        params = {"autostart": "true" if autostart else "false"}
        r = self.client.post(
            f"{self.base_url}/api/download/",
            params=params,
            json=body,
        )
        if r.status_code >= 400:
            raise TAError(
                f"queue_videos failed ({r.status_code}): {r.text[:300]}"
            )
        log.info(
            "queued %d videos to TubeArchivist (autostart=%s)",
            len(video_ids),
            autostart,
        )
