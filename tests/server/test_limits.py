"""Covers server/limits.py and where the routes apply it: per-pilot rate limits on
/llm/api/chat and /tts (429 + Retry-After), and the daily speech character guard."""
import uuid

import pytest
from fastapi.testclient import TestClient

from db.models import User
from server.dependencies import get_current_user
from server.limits import DailyCounter, RateLimiter
from server.main import app
from server.routes import llm as llm_route
from server.routes import tts as tts_route

USER_ID = uuid.uuid4()


def test_rate_limiter_allows_up_to_the_limit_then_says_when_to_retry():
    limiter = RateLimiter(max_events=2, window_s=60)

    assert limiter.retry_after_s("pilot", now=0) is None
    assert limiter.retry_after_s("pilot", now=10) is None
    assert limiter.retry_after_s("pilot", now=20) == pytest.approx(40)
    # Each pilot has their own window.
    assert limiter.retry_after_s("other", now=20) is None
    # Once the first event leaves the window, there's room again.
    assert limiter.retry_after_s("pilot", now=61) is None


def test_a_refused_event_isnt_counted():
    limiter = RateLimiter(max_events=1, window_s=60)
    limiter.retry_after_s("pilot", now=0)
    for second in range(1, 50):
        limiter.retry_after_s("pilot", now=second)
    assert limiter.retry_after_s("pilot", now=60) is None


def test_daily_counter_keeps_a_total_per_key():
    counter = DailyCounter()
    counter.add("pilot", 120)
    counter.add("pilot", 30)
    assert counter.total("pilot") == 150
    assert counter.total("other") == 0


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: User(id=USER_ID, discord_id="123")
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_llm_route_returns_429_with_retry_after_over_the_limit(monkeypatch, client):
    monkeypatch.setattr(llm_route, "rate_limiter", RateLimiter(max_events=0, window_s=60))

    response = client.post("/llm/api/chat", json={"model": "alice-chat", "messages": []})

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_tts_route_stops_at_the_daily_character_guard(monkeypatch, client):
    async def fake_speak(text, user_id):
        from server.tts_service import Speech
        return Speech(audio=b"RIFF", media_type="audio/wav", backend="heket")

    monkeypatch.setattr(tts_route.tts_service, "speak", fake_speak)
    monkeypatch.setattr(tts_route, "rate_limiter", RateLimiter(max_events=100, window_s=60))
    monkeypatch.setattr(tts_route, "daily_chars", DailyCounter())
    monkeypatch.setattr(tts_route.settings, "tts_daily_chars_per_user", 10)

    first = client.post("/tts", json={"text": "Hello."})       # 6 chars, total 6
    second = client.post("/tts", json={"text": "Hello."})      # would be 12 > 10

    assert first.status_code == 200
    assert second.status_code == 429
