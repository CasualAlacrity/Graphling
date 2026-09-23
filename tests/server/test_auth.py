"""Covers the auth relay's shape at the HTTP level — the login redirect and the
protected-route guard — same style as Project Lyra's own test_auth.py, the prior art
this relay is modeled on. The full Discord exchange (server/routes/auth.discord_
callback) needs a real or faked Discord + DB round trip and isn't covered here; this is
the cheap, fast layer: does the right redirect happen, and is get_current_user actually
wired in front of the routes that need it.
"""
from fastapi.testclient import TestClient

from server.main import app

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
