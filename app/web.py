"""FastAPI web app: dashboard, settings, channel management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .state import State
from .subscriptions import parse_opml
from .ta_client import TAClient, TAError
from .youtube_resolve import ChannelResolver, ResolveError

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class SettingsPayload(BaseModel):
    ta_url: str = ""
    ta_token: Optional[str] = None
    scan_interval_minutes: int = Field(default=60, ge=5, le=10080)
    autostart: bool = False
    max_age_days: int = Field(default=7, ge=1, le=365)
    source: str = "db"
    verify_ssl: bool = True
    skip_shorts: bool = True
    skip_streams: bool = False
    slack_webhook_url: str = ""


class AddChannelPayload(BaseModel):
    query: str = Field(min_length=1)


class ToggleChannelPayload(BaseModel):
    enabled: bool


def _scrub(settings: Settings) -> dict[str, Any]:
    return {
        "ta_url": settings.ta_url,
        "ta_token_set": bool(settings.ta_token),
        "scan_interval_minutes": settings.scan_interval_minutes,
        "autostart": settings.autostart,
        "max_age_days": settings.max_age_days,
        "source": settings.source,
        "verify_ssl": settings.verify_ssl,
        "skip_shorts": settings.skip_shorts,
        "skip_streams": settings.skip_streams,
        "opml_path": settings.opml_path,
        "channels_path": settings.channels_path,
        "slack_webhook_url": settings.slack_webhook_url,
    }


def build_app(
    state: State,
    settings_provider,
    ta_provider,
    trigger_scan,
    reschedule,
    next_scan_provider=None,
) -> FastAPI:
    app = FastAPI(title="ta-subscriber", docs_url=None, redoc_url=None)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        s = settings_provider()
        last = state.last_scan()
        next_at = next_scan_provider() if next_scan_provider else None
        return {
            "ready": s.is_ready(),
            "settings": _scrub(s),
            "channels": {
                "total": state.count_channels(),
                "enabled": state.count_channels(enabled_only=True),
            },
            "videos": {
                "seen": state.total_seen(),
                "queued": state.total_queued(),
            },
            "last_scan": last,
            "next_scan_at": next_at,
        }

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return _scrub(settings_provider())

    @app.post("/api/settings")
    def update_settings(payload: SettingsPayload) -> dict[str, Any]:
        state.set_setting("ta_url", payload.ta_url.rstrip("/"))
        if payload.ta_token is not None and payload.ta_token != "":
            state.set_setting("ta_token", payload.ta_token)
        state.set_setting("scan_interval_minutes", payload.scan_interval_minutes)
        state.set_setting("autostart", payload.autostart)
        state.set_setting("max_age_days", payload.max_age_days)
        if payload.source not in {"db", "opml", "file", "env", "ta"}:
            raise HTTPException(400, "invalid source")
        state.set_setting("source", payload.source)
        state.set_setting("verify_ssl", payload.verify_ssl)
        state.set_setting("skip_shorts", payload.skip_shorts)
        state.set_setting("skip_streams", payload.skip_streams)
        state.set_setting("slack_webhook_url", payload.slack_webhook_url.strip())
        reschedule()
        return _scrub(settings_provider())

    @app.post("/api/settings/test")
    def test_settings(payload: Optional[SettingsPayload] = None) -> dict[str, Any]:
        s = settings_provider()
        url = (payload.ta_url.rstrip("/") if payload and payload.ta_url else s.ta_url)
        token = (
            payload.ta_token
            if payload and payload.ta_token
            else s.ta_token
        )
        verify = (
            payload.verify_ssl if payload is not None else s.verify_ssl
        )
        if not url or not token:
            raise HTTPException(400, "ta_url and ta_token required")
        ta = TAClient(url, token, verify=verify)
        try:
            ok, error = ta.ping()
        finally:
            ta.close()
        return {"ok": ok, "error": error}

    @app.get("/api/channels")
    def list_channels() -> list[dict[str, Any]]:
        return state.list_channels()

    @app.post("/api/channels")
    def add_channel(payload: AddChannelPayload) -> dict[str, Any]:
        resolver = ChannelResolver()
        try:
            resolved = resolver.resolve(payload.query)
        except ResolveError as exc:
            raise HTTPException(400, str(exc))
        finally:
            resolver.close()

        added = state.add_channel(
            channel_id=resolved.channel_id,
            name=resolved.name,
            handle=resolved.handle,
        )
        if not added:
            state.upsert_channel(
                channel_id=resolved.channel_id,
                name=resolved.name,
                handle=resolved.handle,
            )
        return {
            "channel_id": resolved.channel_id,
            "name": resolved.name,
            "handle": resolved.handle,
            "added": added,
        }

    @app.delete("/api/channels/{channel_id}")
    def remove_channel(channel_id: str) -> dict[str, Any]:
        ok = state.remove_channel(channel_id)
        if not ok:
            raise HTTPException(404, "channel not found")
        return {"removed": channel_id}

    @app.patch("/api/channels/{channel_id}")
    def toggle_channel(
        channel_id: str, payload: ToggleChannelPayload
    ) -> dict[str, Any]:
        ok = state.set_channel_enabled(channel_id, payload.enabled)
        if not ok:
            raise HTTPException(404, "channel not found")
        return {"channel_id": channel_id, "enabled": payload.enabled}

    @app.post("/api/channels/import-opml")
    async def import_opml(file: UploadFile = File(...)) -> dict[str, Any]:
        content = await file.read()
        try:
            channels = parse_opml(content)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"failed to parse OPML: {exc}")
        added = 0
        for ch in channels:
            if state.add_channel(ch.channel_id, ch.name, None):
                added += 1
        return {"parsed": len(channels), "added": added}

    @app.post("/api/scan")
    def scan_now() -> dict[str, Any]:
        result = trigger_scan()
        return result

    @app.get("/api/scans")
    def recent_scans(limit: int = 20) -> list[dict[str, Any]]:
        return state.recent_scans(limit=limit)

    @app.delete("/api/scans")
    def clear_scans() -> dict[str, Any]:
        return {"deleted": state.clear_scans()}

    @app.get("/api/activity")
    def recent_activity(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        return {
            "items": state.recent_queued(limit=limit, offset=offset),
            "total": state.total_queued(),
            "limit": limit,
            "offset": offset,
        }

    @app.delete("/api/activity")
    def clear_activity() -> dict[str, Any]:
        return {"deleted": state.clear_seen()}

    @app.post("/api/channels/sync-from-ta")
    def sync_from_ta() -> dict[str, Any]:
        s = settings_provider()
        if not s.is_ready():
            raise HTTPException(400, "TA not configured")
        ta = ta_provider()
        try:
            items = ta.list_subscribed_channels()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"TA request failed: {exc}")
        added = 0
        for item in items:
            cid = item.get("channel_id", "")
            if not cid.startswith("UC"):
                continue
            if state.add_channel(cid, item.get("channel_name"), None):
                added += 1
        return {"fetched": len(items), "added": added}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(TAError)
    async def ta_error_handler(_request, exc: TAError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    return app
