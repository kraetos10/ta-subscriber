from datetime import datetime, timezone

import httpx
import respx

from app.slack import build_message, post_new_video, youtube_url

HOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


def test_build_message_puts_bare_youtube_url_on_its_own_line():
    msg = build_message(
        title="My Video",
        channel_name="Some Channel",
        video_id="abc123",
        ta_url="http://ta:8000",
        published=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    lines = msg["text"].splitlines()
    # Bare URL (not a labelled link) is what triggers Slack's rich unfurl.
    assert youtube_url("abc123") in lines
    assert "Some Channel" in msg["text"]
    assert "2026-07-16" in msg["text"]
    assert "/video/abc123/|Open in TubeArchivist>" in msg["text"]


def test_build_message_escapes_slack_mrkdwn_specials():
    msg = build_message(
        title="Cats & Dogs <live>",
        channel_name="A & B",
        video_id="xyz",
        ta_url=None,
        published=None,
    )
    assert "Cats &amp; Dogs &lt;live&gt;" in msg["text"]
    assert "A &amp; B" in msg["text"]


@respx.mock
def test_post_new_video_success():
    route = respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))
    ok = post_new_video(
        HOOK, title="Vid", channel_name="Chan", video_id="xyz", ta_url="http://ta:8000"
    )
    assert ok is True
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "watch?v=xyz" in body


@respx.mock
def test_post_new_video_swallows_http_error():
    respx.post(HOOK).mock(return_value=httpx.Response(500, text="boom"))
    ok = post_new_video(HOOK, title="Vid", channel_name=None, video_id="xyz")
    assert ok is False


@respx.mock
def test_post_new_video_swallows_transport_error():
    respx.post(HOOK).mock(side_effect=httpx.ConnectError("down"))
    ok = post_new_video(HOOK, title="Vid", channel_name=None, video_id="xyz")
    assert ok is False


def test_post_new_video_noops_on_empty_webhook():
    assert post_new_video("", title="Vid", channel_name=None, video_id="xyz") is False
