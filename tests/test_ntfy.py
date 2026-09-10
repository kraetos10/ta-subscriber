from datetime import datetime, timezone

import httpx
import respx

from app.ntfy import (
    build_message,
    normalise,
    post_new_video,
    thumbnail_url,
    youtube_url,
)

SERVER = "https://ntfy.example.com"
ENDPOINT = SERVER + "/"
TOPIC = "ta-subscriber"


def test_build_message_carries_topic_click_and_actions():
    msg = build_message(
        title="My Video",
        channel_name="Some Channel",
        video_id="abc123",
        topic=TOPIC,
        ta_url="http://ta:8000",
        published=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    assert msg["topic"] == TOPIC
    assert msg["title"] == "My Video"
    assert msg["message"] == "Some Channel · 2026-07-16"
    # Tapping the notification opens the archived copy in TA.
    assert msg["click"] == "http://ta:8000/video/abc123/"
    assert msg["attach"] == thumbnail_url("abc123")
    labels = [a["label"] for a in msg["actions"]]
    assert labels == ["YouTube", "TubeArchivist"]
    assert msg["actions"][1]["url"] == "http://ta:8000/video/abc123/"


def test_build_message_without_metadata_or_ta_url():
    msg = build_message(
        title="My Video",
        channel_name=None,
        video_id="xyz",
        topic=TOPIC,
        ta_url=None,
        published=None,
    )
    # message is required by ntfy, so it always has a body.
    assert msg["message"] == "New video added to queue"
    assert [a["label"] for a in msg["actions"]] == ["YouTube"]
    # With no TA URL to point at, the click falls back to YouTube.
    assert msg["click"] == youtube_url("xyz")


def test_build_message_adds_a_download_action_when_a_link_is_given():
    msg = build_message(
        title="My Video",
        channel_name=None,
        video_id="abc123",
        topic=TOPIC,
        ta_url="http://ta:8000",
        published=None,
        download_url="https://sub.example/api/videos/abc123/download?t=sig",
    )
    download = msg["actions"][-1]
    assert download["label"] == "Download"
    # An http action POSTs in the background; a view action would only open it.
    assert download["action"] == "http"
    assert download["method"] == "POST"
    assert download["url"].endswith("?t=sig")
    # ntfy caps action buttons at three.
    assert len(msg["actions"]) == 3


def test_build_message_omits_download_action_without_a_link():
    msg = build_message(
        title="My Video",
        channel_name=None,
        video_id="abc123",
        topic=TOPIC,
        ta_url="http://ta:8000",
        published=None,
    )
    assert [a["label"] for a in msg["actions"]] == ["YouTube", "TubeArchivist"]


def test_normalise_accepts_a_pasted_topic_url():
    assert normalise("https://ntfy.sh/my-topic", "") == ("https://ntfy.sh/", "my-topic")
    # An explicit topic wins over one in the server URL's path.
    assert normalise("https://ntfy.sh/ignored", "real") == ("https://ntfy.sh/", "real")


def test_normalise_defaults_scheme_and_server():
    assert normalise("ntfy.example.com", "t") == ("https://ntfy.example.com/", "t")
    assert normalise("", "t") == ("https://ntfy.sh/", "t")


@respx.mock
def test_post_new_video_success_without_token():
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, text="{}"))
    ok = post_new_video(
        SERVER, TOPIC, title="Vid", channel_name="Chan", video_id="xyz",
        ta_url="http://ta:8000",
    )
    assert ok is True
    request = route.calls.last.request
    assert b'"topic":"ta-subscriber"' in request.content.replace(b" ", b"")
    assert "authorization" not in request.headers


@respx.mock
def test_post_new_video_sends_bearer_token():
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200))
    ok = post_new_video(
        SERVER, TOPIC, title="Vid", channel_name=None, video_id="xyz", token="tk_abc"
    )
    assert ok is True
    assert route.calls.last.request.headers["authorization"] == "Bearer tk_abc"


@respx.mock
def test_post_new_video_swallows_http_error():
    respx.post(ENDPOINT).mock(return_value=httpx.Response(401, text="unauthorized"))
    assert post_new_video(SERVER, TOPIC, title="V", channel_name=None, video_id="x") is False


@respx.mock
def test_post_new_video_swallows_transport_error():
    respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError("down"))
    assert post_new_video(SERVER, TOPIC, title="V", channel_name=None, video_id="x") is False


def test_post_new_video_noops_on_empty_topic():
    assert post_new_video(SERVER, "", title="V", channel_name=None, video_id="x") is False
