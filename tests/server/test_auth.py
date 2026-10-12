"""Covers the auth relay's shape at the HTTP level — the login redirect and the
protected-route guard — same style as Project Lyra's own test_auth.py, the prior art
this relay is modeled on. The full Discord exchange (server/routes/auth.discord_
callback) needs a real or faked Discord + DB round trip and isn't covered here; this is
the cheap, fast layer: does the right redirect happen, and is get_current_user actually
wired in front of the routes that need it.
"""
import pytest
from fastapi.testclient import TestClient

from server.config import settings
from server.main import app
from server.routes import auth

client = TestClient(app)


def test_users_me_without_a_token_is_rejected():
    response = client.get("/auth/users/me")

    assert response.status_code in (401, 403)


def test_discord_login_redirects_to_discord():
    response = client.get(
        "/auth/discord/login",
        params={"redirect_uri": "http://localhost:8765/callback", "client_state": "abc"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "discord.com" in response.headers["location"]


def test_discord_callback_rejects_an_unrecognized_state():
    response = client.get("/auth/discord/callback", params={"code": "some-code", "state": "never-issued"})

    assert response.status_code == 400


@pytest.mark.parametrize("redirect_uri", [
    "http://localhost:8765/callback",
    "http://127.0.0.1:8765/callback",
    "http://127.0.0.1:51234/",
])
def test_discord_login_allows_loopback_redirects(redirect_uri):
    response = client.get(
        "/auth/discord/login",
        params={"redirect_uri": redirect_uri, "client_state": "abc"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "discord.com" in response.headers["location"]


@pytest.mark.parametrize("redirect_uri", [
    "https://evil.example/steal",
    "http://evil.example:8765/callback",
    "https://localhost:8765/callback",              # loopback is http only
    "http://localhost.evil.example:8765/callback",
    "http://localhost:8765@evil.example/callback",  # userinfo trick: real host is evil.example
    "http://evil.example\\@localhost:8765/",        # browsers treat a backslash as /, host evil.example
    "//evil.example/callback",
    "javascript:alert(1)",
    "http://localhost:notaport/",
    "https://heyalice.help/callback",               # not configured in this test
])
def test_discord_login_rejects_disallowed_redirects(redirect_uri):
    pending_before = dict(auth._PENDING_LOGINS)

    response = client.get(
        "/auth/discord/login",
        params={"redirect_uri": redirect_uri, "client_state": "abc"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert auth._PENDING_LOGINS == pending_before  # nothing stashed for the callback to use


def test_discord_login_allows_a_configured_origin(monkeypatch):
    monkeypatch.setattr(settings, "auth_allowed_redirect_origins", ["https://heyalice.help"])

    allowed = client.get(
        "/auth/discord/login",
        params={"redirect_uri": "https://heyalice.help/auth/done", "client_state": "abc"},
        follow_redirects=False,
    )
    other_scheme = client.get(
        "/auth/discord/login",
        params={"redirect_uri": "http://heyalice.help/auth/done", "client_state": "abc"},
        follow_redirects=False,
    )

    assert allowed.status_code in (302, 307)
    assert other_scheme.status_code == 400
