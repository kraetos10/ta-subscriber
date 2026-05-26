"""Load the list of YouTube channel IDs to monitor."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .state import State

log = logging.getLogger(__name__)

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


@dataclass(frozen=True)
class Channel:
    channel_id: str
    name: str | None = None


def load(settings: Settings, state: State, ta_client=None) -> list[Channel]:
    source = settings.source
    if source == "db":
        return _load_db(state)
    if source == "opml":
        return _load_opml(settings.opml_path)
    if source == "file":
        return _load_file(settings.channels_path)
    if source == "env":
        return _load_env(settings.channel_ids_env)
    if source == "ta":
        if ta_client is None:
            raise RuntimeError("ta source requires ta_client")
        return _load_ta(ta_client)
    raise RuntimeError(f"unknown source: {source}")


def _load_db(state: State) -> list[Channel]:
    rows = state.list_channels(enabled_only=True)
    channels = [
        Channel(channel_id=r["channel_id"], name=r.get("name"))
        for r in rows
        if CHANNEL_ID_RE.match(r["channel_id"])
    ]
    log.info("loaded %d channels from DB", len(channels))
    return channels


def parse_opml(content: bytes | str) -> list[Channel]:
    root = ET.fromstring(content)
    channels: list[Channel] = []
    for outline in root.iter("outline"):
        xml_url = outline.attrib.get("xmlUrl")
        if not xml_url:
            continue
        cid = extract_channel_id(xml_url)
        if cid:
            channels.append(
                Channel(channel_id=cid, name=outline.attrib.get("title"))
            )
    return _dedupe(channels)


def _load_opml(path: str) -> list[Channel]:
    p = Path(path)
    if not p.exists():
        log.error("OPML file not found: %s", path)
        return []
    channels = parse_opml(p.read_bytes())
    log.info("loaded %d channels from OPML", len(channels))
    return channels


def _load_file(path: str) -> list[Channel]:
    p = Path(path)
    if not p.exists():
        log.error("channels file not found: %s", path)
        return []
    channels: list[Channel] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cid = normalise(line)
        if cid:
            channels.append(Channel(channel_id=cid))
    log.info("loaded %d channels from file", len(channels))
    return _dedupe(channels)


def _load_env(raw: str) -> list[Channel]:
    channels: list[Channel] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        cid = normalise(token)
        if cid:
            channels.append(Channel(channel_id=cid))
    log.info("loaded %d channels from env", len(channels))
    return _dedupe(channels)


def _load_ta(ta_client) -> list[Channel]:
    items = ta_client.list_subscribed_channels()
    channels = [
        Channel(channel_id=i["channel_id"], name=i.get("channel_name"))
        for i in items
        if CHANNEL_ID_RE.match(i.get("channel_id", ""))
    ]
    log.info("loaded %d channels from TubeArchivist", len(channels))
    return _dedupe(channels)


def extract_channel_id(value: str) -> str | None:
    if CHANNEL_ID_RE.match(value):
        return value
    parsed = urlparse(value)
    qs = parse_qs(parsed.query)
    cid = (qs.get("channel_id") or [None])[0]
    if cid and CHANNEL_ID_RE.match(cid):
        return cid
    if parsed.path.startswith("/channel/"):
        cid = parsed.path.split("/channel/", 1)[1].split("/", 1)[0]
        if CHANNEL_ID_RE.match(cid):
            return cid
    return None


def normalise(value: str) -> str | None:
    return extract_channel_id(value) or (
        value if CHANNEL_ID_RE.match(value) else None
    )


def _dedupe(channels: list[Channel]) -> list[Channel]:
    seen: set[str] = set()
    out: list[Channel] = []
    for ch in channels:
        if ch.channel_id in seen:
            continue
        seen.add(ch.channel_id)
        out.append(ch)
    return out
