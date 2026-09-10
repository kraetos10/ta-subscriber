"""Signed one-shot URLs for notification action buttons.

The ntfy "Download" button fires an unauthenticated POST from a phone, so the
URL itself has to carry the authority. Each link is an HMAC over a single
video ID, which makes it useless for anything but downloading that one video —
the TubeArchivist token stays on the server.
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

# YouTube IDs are 11 chars, but stay lenient; this is a sanity bound, not a
# parser, and it keeps caller-supplied IDs out of the TA URL path.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

SIG_LEN = 32


def valid_video_id(video_id: str) -> bool:
    return bool(VIDEO_ID_RE.match(video_id))


def sign(secret: str, video_id: str) -> str:
    """Return the signature authorising a download of this video."""
    mac = hmac.new(secret.encode(), f"download:{video_id}".encode(), sha256)
    return mac.hexdigest()[:SIG_LEN]


def verify(secret: str, video_id: str, signature: str) -> bool:
    if not secret or not signature:
        return False
    return hmac.compare_digest(sign(secret, video_id), signature)


def download_url(public_url: str, secret: str, video_id: str) -> str | None:
    """Build the signed download link, or None if it can't be built.

    Returns None when the instance has no public URL configured — without one
    there is no address a phone could reach, so the button is simply omitted.
    """
    base = (public_url or "").strip().rstrip("/")
    if not base or not secret or not valid_video_id(video_id):
        return None
    return f"{base}/api/videos/{video_id}/download?t={sign(secret, video_id)}"
