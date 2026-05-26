"""Configuration: env vars seed the database on first run; DB is the source of truth thereafter."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from .state import State

SourceKind = Literal["db", "opml", "file", "env", "ta"]


SETTING_KEYS: dict[str, Any] = {
    "ta_url": "",
    "ta_token": "",
    "scan_interval_minutes": 60,
    "autostart": False,
    "max_age_days": 7,
    "source": "db",
    "verify_ssl": True,
    "skip_shorts": True,
    "skip_streams": False,
    "opml_path": "/data/subscriptions.opml",
    "channels_path": "/data/channels.txt",
    "channel_ids_env": "",
}


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    ta_url: str
    ta_token: str
    scan_interval_minutes: int
    autostart: bool
    max_age_days: int
    source: SourceKind
    verify_ssl: bool
    skip_shorts: bool
    skip_streams: bool
    opml_path: str
    channels_path: str
    channel_ids_env: str

    def is_ready(self) -> bool:
        return bool(self.ta_url and self.ta_token)


def bootstrap_settings(state: State) -> None:
    """Seed DB settings from env on first run only."""
    existing = state.all_settings()
    defaults_from_env = {
        "ta_url": os.environ.get("TA_URL", "").rstrip("/"),
        "ta_token": os.environ.get("TA_TOKEN", ""),
        "scan_interval_minutes": _int("SCAN_INTERVAL_MINUTES", 60),
        "autostart": _bool("AUTOSTART", False),
        "max_age_days": _int("MAX_AGE_DAYS", 7),
        "source": os.environ.get("SOURCE", "db").lower(),
        "verify_ssl": _bool("VERIFY_SSL", True),
        "skip_shorts": _bool("SKIP_SHORTS", True),
        "skip_streams": _bool("SKIP_STREAMS", False),
        "opml_path": os.environ.get("OPML_PATH", "/data/subscriptions.opml"),
        "channels_path": os.environ.get("CHANNELS_PATH", "/data/channels.txt"),
        "channel_ids_env": os.environ.get("CHANNEL_IDS", ""),
    }
    for key, default in SETTING_KEYS.items():
        if key in existing:
            continue
        value = defaults_from_env.get(key, default)
        state.set_setting(key, value)


def load_settings(state: State) -> Settings:
    raw = state.all_settings()
    return Settings(
        ta_url=str(raw.get("ta_url") or "").rstrip("/"),
        ta_token=str(raw.get("ta_token") or ""),
        scan_interval_minutes=int(raw.get("scan_interval_minutes") or 60),
        autostart=bool(raw.get("autostart")),
        max_age_days=int(raw.get("max_age_days") or 7),
        source=str(raw.get("source") or "db"),  # type: ignore[arg-type]
        verify_ssl=bool(raw.get("verify_ssl", True)),
        skip_shorts=bool(raw.get("skip_shorts", True)),
        skip_streams=bool(raw.get("skip_streams", False)),
        opml_path=str(raw.get("opml_path") or "/data/subscriptions.opml"),
        channels_path=str(raw.get("channels_path") or "/data/channels.txt"),
        channel_ids_env=str(raw.get("channel_ids_env") or ""),
    )


def runtime() -> dict[str, str]:
    return {
        "state_dir": os.environ.get("STATE_DIR", "/data"),
        "log_level": os.environ.get("LOG_LEVEL", "INFO").upper(),
        "host": os.environ.get("HOST", "0.0.0.0"),
        "port": os.environ.get("PORT", "8080"),
    }
