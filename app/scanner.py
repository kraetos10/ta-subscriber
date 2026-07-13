"""Orchestrate one scan cycle: load subs, fetch RSS, queue new videos."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

import httpx

from .config import Settings
from .state import State
from .subscriptions import load as load_subscriptions
from .ta_client import TAClient
from .youtube_rss import RSSFetcher, VideoEntry, classify_video

log = logging.getLogger(__name__)


class Scanner:
    def __init__(
        self,
        settings_provider,
        state: State,
        ta_provider,
    ) -> None:
        """settings_provider() -> Settings (latest), ta_provider() -> TAClient (latest)."""
        self._settings_provider = settings_provider
        self.state = state
        self._ta_provider = ta_provider
        self._lock = Lock()

    def scan_once(self) -> dict[str, int | str]:
        if not self._lock.acquire(blocking=False):
            log.info("scan already in progress, skipping")
            return {"skipped": "in_progress"}
        scan_id = self.state.start_scan()
        try:
            return self._do_scan(scan_id)
        finally:
            self._lock.release()

    def _do_scan(self, scan_id: int) -> dict[str, int | str]:
        settings = self._settings_provider()
        if not settings.is_ready():
            err = "TA_URL and TA_TOKEN must be set"
            log.warning(err)
            self.state.finish_scan(scan_id, 0, 0, 0, 0, error=err)
            return {"error": err}

        ta = self._ta_provider()
        try:
            channels = load_subscriptions(settings, self.state, ta_client=ta)
        except Exception as exc:  # noqa: BLE001
            log.exception("failed to load subscriptions")
            self.state.finish_scan(scan_id, 0, 0, 0, 0, error=str(exc))
            return {"error": str(exc)}

        if not channels:
            log.warning("no channels to scan")
            self.state.finish_scan(scan_id, 0, 0, 0, 0, 0, error=None)
            return {"channels": 0, "candidates": 0, "new": 0, "queued": 0}

        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.max_age_days)

        candidates: list[VideoEntry] = []
        with httpx.Client(
            headers={"User-Agent": "ta-subscriber/1.0"},
            timeout=20.0,
        ) as http:
            fetcher = RSSFetcher(http)

            def fetch_channel(ch):
                entries = fetcher.fetch(ch.channel_id)
                return [e for e in entries if e.published >= cutoff]

            with ThreadPoolExecutor(max_workers=10) as pool:
                for fresh in pool.map(fetch_channel, channels):
                    candidates.extend(fresh)

        unseen_ids = self.state.filter_unseen([c.video_id for c in candidates])
        unseen_set = set(unseen_ids)
        unseen = [c for c in candidates if c.video_id in unseen_set]

        to_queue: list[VideoEntry] = []
        skipped: list[tuple[VideoEntry, str]] = []

        if unseen and (settings.skip_shorts or settings.skip_streams):
            with httpx.Client(
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ta-subscriber/1.0)",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                cookies={"CONSENT": "YES+1", "SOCS": "CAI"},
                timeout=15.0,
            ) as classify_http:
                def classify(entry):
                    return entry, classify_video(
                        classify_http,
                        entry.video_id,
                        detect_streams=settings.skip_streams,
                    )

                with ThreadPoolExecutor(max_workers=10) as pool:
                    for entry, kind in pool.map(classify, unseen):
                        if kind == "shorts" and settings.skip_shorts:
                            skipped.append((entry, "shorts"))
                        elif kind == "stream" and settings.skip_streams:
                            skipped.append((entry, "stream"))
                        else:
                            to_queue.append(entry)
        else:
            to_queue = list(unseen)

        queued_ids: list[str] = []
        unconfirmed = 0
        error: str | None = None
        if to_queue:
            try:
                ta.queue_videos(
                    video_ids=[c.video_id for c in to_queue],
                    autostart=settings.autostart,
                )
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                log.error("failed to queue videos: %s", exc)
            else:
                # TA extracts asynchronously and silently drops videos it can't
                # ingest yet (e.g. freshly-ended live streams still processing).
                # Only mark videos seen once TA confirms it took them; leave the
                # rest unseen so the next scan retries them (bounded by max_age).
                confirmed = self._confirm_ingested(
                    ta, [c.video_id for c in to_queue]
                )
                queued_ids = [c.video_id for c in to_queue if c.video_id in confirmed]
                unconfirmed = len(to_queue) - len(queued_ids)
                if unconfirmed:
                    log.info(
                        "%d/%d videos not yet ingested by TA; will retry next scan",
                        unconfirmed,
                        len(to_queue),
                    )

        now_ts = int(time.time())
        if queued_ids:
            queued_set = set(queued_ids)
            queued_pairs = [
                (c.video_id, c.channel_id, c.title, int(c.published.timestamp()))
                for c in to_queue
                if c.video_id in queued_set
            ]
            self.state.mark_seen(queued_pairs, queued=True, now_ts=now_ts)
        if skipped:
            by_reason: dict[str, list[tuple[str, str, str, int]]] = {}
            for entry, reason in skipped:
                by_reason.setdefault(reason, []).append(
                    (
                        entry.video_id,
                        entry.channel_id,
                        entry.title,
                        int(entry.published.timestamp()),
                    )
                )
            for reason, items in by_reason.items():
                self.state.mark_seen(
                    items, queued=False, now_ts=now_ts, skipped_reason=reason
                )

        self.state.finish_scan(
            scan_id,
            channels=len(channels),
            candidates=len(candidates),
            new_videos=len(unseen),
            queued=len(queued_ids),
            skipped=len(skipped),
            error=error,
        )

        log.info(
            "scan complete: %d channels, %d candidates, %d new, %d queued, %d skipped",
            len(channels),
            len(candidates),
            len(unseen),
            len(queued_ids),
            len(skipped),
        )
        return {
            "channels": len(channels),
            "candidates": len(candidates),
            "new": len(unseen),
            "queued": len(queued_ids),
            "skipped": len(skipped),
            **({"error": error} if error else {}),
        }

    def _confirm_ingested(
        self,
        ta: TAClient,
        video_ids: list[str],
        attempts: int = 3,
        delay: float = 8.0,
    ) -> set[str]:
        """Return the subset of video_ids TA has actually ingested.

        Polls briefly because TA extracts asynchronously: already-available
        videos (normal uploads) confirm on the first round, while videos still
        processing on YouTube's side stay unconfirmed and are retried on the
        next scan cycle rather than being marked seen prematurely.
        """
        remaining = list(video_ids)
        confirmed: set[str] = set()
        for attempt in range(attempts):
            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(ta.is_ingested, remaining))
            newly = {vid for vid, ok in zip(remaining, results) if ok}
            confirmed |= newly
            remaining = [vid for vid in remaining if vid not in newly]
            if not remaining or attempt == attempts - 1:
                break
            time.sleep(delay)
        return confirmed
