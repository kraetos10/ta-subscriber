"""The "Send test notification" button: per-channel results."""

import tempfile

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import bootstrap_settings, load_settings
from app.state import State
from app.web import build_app

HOOK = "https://hooks.slack.com/services/T000/B000/XXXX"
NTFY = "https://ntfy.sh/"


@pytest.fixture
def client():
    state = State(tempfile.mkdtemp())
    bootstrap_settings(state)
    app = build_app(
        state, lambda: load_settings(state), lambda: None, lambda: {}, lambda: None
    )
    return TestClient(app)


@respx.mock
def test_sends_to_both_configured_channels(client):
    slack = respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))
    ntfy = respx.post(NTFY).mock(return_value=httpx.Response(200))
    r = client.post(
        "/api/notify/test",
        json={"slack_webhook_url": HOOK, "ntfy_topic": "vids"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "slack": {"ok": True, "error": None},
        "ntfy": {"ok": True, "error": None},
    }
    assert slack.called and ntfy.called
    # The test message is a plain notice, not a fake video.
    assert b"Test notification" in ntfy.calls.last.request.content


@respx.mock
def test_reports_each_channel_independently(client):
    respx.post(HOOK).mock(return_value=httpx.Response(200))
    respx.post(NTFY).mock(return_value=httpx.Response(403, text="forbidden"))
    r = client.post(
        "/api/notify/test",
        json={"slack_webhook_url": HOOK, "ntfy_topic": "vids"},
    )
    body = r.json()
    assert body["slack"]["ok"] is True
    assert body["ntfy"]["ok"] is False
    assert "403" in body["ntfy"]["error"]


@respx.mock
def test_only_tests_what_is_configured(client):
    ntfy = respx.post(NTFY).mock(return_value=httpx.Response(200))
    r = client.post("/api/notify/test", json={"ntfy_topic": "vids"})
    assert list(r.json()) == ["ntfy"]
    assert ntfy.called


@respx.mock
def test_transport_error_is_reported_not_raised(client):
    respx.post(NTFY).mock(side_effect=httpx.ConnectError("no route to host"))
    r = client.post("/api/notify/test", json={"ntfy_topic": "vids"})
    assert r.status_code == 200
    assert r.json()["ntfy"]["ok"] is False
    assert "no route" in r.json()["ntfy"]["error"]


def test_400_when_nothing_is_configured(client):
    r = client.post("/api/notify/test", json={"ta_url": "http://ta:8000"})
    assert r.status_code == 400


@respx.mock
def test_falls_back_to_saved_settings_when_no_payload(client):
    client.post("/api/settings", json={"ta_url": "http://ta:8000", "ntfy_topic": "saved"})
    ntfy = respx.post(NTFY).mock(return_value=httpx.Response(200))
    r = client.post("/api/notify/test")
    assert r.status_code == 200
    assert b'"topic":"saved"' in ntfy.calls.last.request.content.replace(b" ", b"")
