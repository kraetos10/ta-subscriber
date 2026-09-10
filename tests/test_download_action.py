"""The signed Download button: link building, and the endpoint it calls."""

import tempfile

import pytest
from fastapi.testclient import TestClient

from app.config import bootstrap_settings, load_settings
from app.signing import download_url, sign, valid_video_id, verify
from app.state import State
from app.web import build_app

SECRET = "s3cret"


def test_signature_is_scoped_to_one_video():
    sig = sign(SECRET, "abc123")
    assert verify(SECRET, "abc123", sig)
    # A link leaked from one notification cannot download a different video.
    assert not verify(SECRET, "other1", sig)
    assert not verify("wrong-secret", "abc123", sig)
    assert not verify(SECRET, "abc123", "")
    assert not verify("", "abc123", sig)


def test_valid_video_id_rejects_path_traversal():
    assert valid_video_id("dQw4w9WgXcQ")
    assert not valid_video_id("../../etc/passwd")
    assert not valid_video_id("abc/def")
    assert not valid_video_id("")


def test_download_url_omitted_without_a_public_url():
    assert download_url("", SECRET, "abc123") is None
    assert download_url("https://sub.example", "", "abc123") is None
    assert download_url("https://sub.example", SECRET, "../etc") is None


def test_download_url_shape():
    url = download_url("https://sub.example/", SECRET, "abc123")
    assert url == (
        f"https://sub.example/api/videos/abc123/download?t={sign(SECRET, 'abc123')}"
    )


class FakeTA:
    def __init__(self, found=True, boom=None):
        self.found, self.boom, self.calls = found, boom, []

    def download_now(self, video_id):
        self.calls.append(video_id)
        if self.boom:
            raise self.boom
        return self.found


@pytest.fixture
def client_and_ta():
    state = State(tempfile.mkdtemp())
    bootstrap_settings(state)
    state.set_setting("ta_url", "http://ta:8000")
    state.set_setting("ta_token", "tok")
    ta = FakeTA()
    app = build_app(
        state,
        lambda: load_settings(state),
        lambda: ta,
        lambda: {},
        lambda: None,
    )
    return TestClient(app), ta, load_settings(state)


def test_endpoint_triggers_download_with_a_valid_signature(client_and_ta):
    client, ta, s = client_and_ta
    sig = sign(s.notify_secret, "abc123")
    r = client.post(f"/api/videos/abc123/download?t={sig}")
    assert r.status_code == 200
    assert r.json() == {"video_id": "abc123", "downloading": True}
    assert ta.calls == ["abc123"]


def test_endpoint_rejects_bad_or_missing_signature(client_and_ta):
    client, ta, s = client_and_ta
    assert client.post("/api/videos/abc123/download").status_code == 403
    assert client.post("/api/videos/abc123/download?t=nope").status_code == 403
    # A signature for a different video must not work here either.
    other = sign(s.notify_secret, "different")
    assert client.post(f"/api/videos/abc123/download?t={other}").status_code == 403
    assert ta.calls == []


def test_endpoint_404s_when_video_left_the_queue(client_and_ta):
    client, ta, s = client_and_ta
    ta.found = False
    sig = sign(s.notify_secret, "abc123")
    assert client.post(f"/api/videos/abc123/download?t={sig}").status_code == 404


def test_endpoint_502s_when_ta_fails(client_and_ta):
    client, ta, s = client_and_ta
    ta.boom = RuntimeError("connection refused")
    sig = sign(s.notify_secret, "abc123")
    assert client.post(f"/api/videos/abc123/download?t={sig}").status_code == 502


def test_secret_is_generated_once_and_never_exposed(client_and_ta):
    client, _, s = client_and_ta
    assert len(s.notify_secret) > 20
    body = client.get("/api/settings").json()
    assert "notify_secret" not in body
    # Nor can it be overwritten through the settings API: a signature made
    # before the attempt still verifies afterwards.
    sig = sign(s.notify_secret, "abc123")
    client.post("/api/settings", json={"ta_url": "http://ta:8000",
                                       "notify_secret": "attacker"})
    assert client.post(f"/api/videos/abc123/download?t={sig}").status_code == 200
