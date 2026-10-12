"""Covers server/tts_service.py and server/routes/tts.py: Kokoro on frankencompute first,
ElevenLabs only when the box can't answer and a key is configured, and one tts_usage row
per attempt. Outbound HTTP goes through httpx.MockTransport via the _http_client seam --
no real box, no real ElevenLabs -- and record_usage is swapped for a list.
"""
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from db.models import User
from server import tts_service
from server.config import settings
from server.dependencies import get_current_user
from server.main import app

USER_ID = uuid.uuid4()
WAV = b"RIFF....WAVEfmt "
MP3 = b"ID3\x03fake-mp3"


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(tts_service, "state", tts_service.TtsState())
    # No test may pick up a real key from .env.
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "")
    monkeypatch.setattr(settings, "elevenlabs_usd_per_1k_chars", 0.0)
    monkeypatch.setattr(settings, "tts_voice", "bf_isabella")


@pytest.fixture
def usage_rows(monkeypatch):
    rows = []

    async def fake_record_usage(**fields):
        rows.append(fields)

    monkeypatch.setattr(tts_service, "record_usage", fake_record_usage)
    return rows


@pytest.fixture
def with_elevenlabs(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "xi-test")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "voice-123")
    monkeypatch.setattr(settings, "elevenlabs_usd_per_1k_chars", 0.04)


def route_http(monkeypatch, handler):
    def fake_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(tts_service, "_http_client", fake_client)


def kokoro_ok(request):
    return httpx.Response(200, content=WAV, headers={"content-type": "audio/wav", "X-Audio-Seconds": "5.62"})


def elevenlabs_ok(request):
    return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})


async def test_kokoro_on_frankencompute_answers_first(monkeypatch, usage_rows):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return kokoro_ok(request)

    route_http(monkeypatch, handler)

    speech = await tts_service.speak("Baijini Point sells Laranite.", USER_ID)

    assert speech == tts_service.Speech(audio=WAV, media_type="audio/wav", backend="frankencompute")
    assert seen["url"].endswith("/tts")
    assert b"bf_isabella" in seen["body"]
    row = usage_rows[0]
    assert (row["backend"], row["status"], row["fallback_reason"]) == ("frankencompute", "ok", None)
    assert row["characters"] == len("Baijini Point sells Laranite.")
    assert row["audio_seconds"] == 5.62
    assert row["cost_usd"] > 0


async def test_unreachable_box_falls_back_to_elevenlabs_and_benches_it(monkeypatch, usage_rows, with_elevenlabs):
    tried = []

    def handler(request):
        tried.append(request.url.host)
        if request.url.host == "api.elevenlabs.io":
            return elevenlabs_ok(request)
        raise httpx.ConnectError("no route to host")

    route_http(monkeypatch, handler)

    speech = await tts_service.speak("What ship are you flying?", USER_ID)
    await tts_service.speak("What ship are you flying?", USER_ID)

    assert speech.backend == "elevenlabs"
    assert speech.media_type == "audio/mpeg"
    # The second request skips the benched box instead of waiting on it again.
    assert tried == ["localhost", "api.elevenlabs.io", "api.elevenlabs.io"]
    assert [row["fallback_reason"] for row in usage_rows] == ["down", "down"]
    assert usage_rows[0]["cost_usd"] == pytest.approx(len("What ship are you flying?") / 1000 * 0.04)


async def test_a_kokoro_5xx_falls_back(monkeypatch, usage_rows, with_elevenlabs):
    def handler(request):
        if request.url.host == "api.elevenlabs.io":
            return elevenlabs_ok(request)
        return httpx.Response(500, json={"error": "synthesis failed"})

    route_http(monkeypatch, handler)

    speech = await tts_service.speak("Hello.", USER_ID)

    assert speech.backend == "elevenlabs"
    attempts = [(row["backend"], row["status"]) for row in usage_rows]
    assert attempts == [("frankencompute", "error"), ("elevenlabs", "ok")]
    assert usage_rows[1]["fallback_reason"] == "5xx"


async def test_a_kokoro_4xx_is_not_retried_on_elevenlabs(monkeypatch, usage_rows, with_elevenlabs):
    tried = []

    def handler(request):
        tried.append(request.url.host)
        return httpx.Response(413, json={"error": "too long"})

    route_http(monkeypatch, handler)

    with pytest.raises(tts_service.NoBackendAvailable):
        await tts_service.speak("x" * 10, USER_ID)
    assert tried == ["localhost"]


async def test_without_elevenlabs_a_down_box_means_no_voice_but_is_retried(monkeypatch, usage_rows):
    tried = []

    def handler(request):
        tried.append(request.url.host)
        raise httpx.ConnectError("no route to host")

    route_http(monkeypatch, handler)

    for _ in range(2):
        with pytest.raises(tts_service.NoBackendAvailable):
            await tts_service.speak("Hello.", USER_ID)
    # No fallback to go to, so the box isn't benched -- every request tries it.
    assert tried == ["localhost", "localhost"]


# ---- route ----

def test_route_requires_authentication():
    response = TestClient(app).post("/tts", json={"text": "Hello."})
    assert response.status_code == 401


def test_route_returns_audio_and_names_the_backend(monkeypatch, usage_rows):
    route_http(monkeypatch, kokoro_ok)
    app.dependency_overrides[get_current_user] = lambda: User(id=USER_ID, discord_id="123")
    try:
        client = TestClient(app)
        response = client.post("/tts", json={"text": "Hello."})
        empty = client.post("/tts", json={"text": "   "})
        too_long = client.post("/tts", json={"text": "x" * 2001})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.content == WAV
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["X-Alice-TTS-Backend"] == "frankencompute"
    assert empty.status_code == 400
    assert too_long.status_code == 413
