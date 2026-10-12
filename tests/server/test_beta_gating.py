"""Beta gating in the Discord callback (server/routes/auth.py, settings.beta_guild_id):
which scope login asks for, and what the callback does with each answer from Discord's
member endpoint. Discord is faked with an httpx MockTransport and the DB with a stub
get_or_create_user, so nothing leaves the process.
"""
import logging
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from server.config import settings
from server.main import app
from server.routes import auth

client = TestClient(app)

GUILD_ID = "738281719819403285"
ROLE_ID = "111"
DISCORD_ID = "424242"
CLIENT_REDIRECT = "http://localhost:8765/callback"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret")


@pytest.fixture
def gating_on(monkeypatch):
    monkeypatch.setattr(settings, "beta_guild_id", GUILD_ID)
    monkeypatch.setattr(settings, "beta_role_id", ROLE_ID)


@pytest.fixture
def created_users(monkeypatch):
    created = []

    class _User:
        id = uuid.uuid4()

    async def fake_get_or_create_user(discord_id):
        created.append(discord_id)
        return _User()

    monkeypatch.setattr(auth, "get_or_create_user", fake_get_or_create_user)
    return created


def _fake_discord(monkeypatch, member_response):
    """Routes the callback's Discord calls to canned answers. member_response is an
    httpx.Response, or an exception to raise, for the guild-member endpoint. Returns the
    list of member-endpoint calls made."""
    member_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == auth.DISCORD_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "discord-access-token"})
        if url == auth.DISCORD_USER_URL:
            return httpx.Response(200, json={"id": DISCORD_ID, "username": "jeff"})
        if url == auth.DISCORD_GUILD_MEMBER_URL.format(guild_id=GUILD_ID):
            member_calls.append(request.headers["Authorization"])
            if isinstance(member_response, Exception):
                raise member_response
            return member_response
        raise AssertionError(f"unexpected Discord call: {url}")

    class _FakeDiscordClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(auth.httpx, "AsyncClient", _FakeDiscordClient)
    return member_calls


def _login_and_callback() -> dict:
    """Runs /discord/login then /discord/callback with the nonce it issued, and returns
    the query params of the final redirect to the client's listener."""
    login = client.get(
        "/auth/discord/login",
        params={"redirect_uri": CLIENT_REDIRECT, "client_state": "client-state"},
        follow_redirects=False,
    )
    nonce = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = client.get("/auth/discord/callback", params={"code": "c", "state": nonce}, follow_redirects=False)
    assert callback.status_code in (302, 307)
    location = callback.headers["location"]
    assert location.startswith(CLIENT_REDIRECT + "?")
    return {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}


def _login_scope() -> str:
    login = client.get(
        "/auth/discord/login",
        params={"redirect_uri": CLIENT_REDIRECT, "client_state": "abc"},
        follow_redirects=False,
    )
    return parse_qs(urlsplit(login.headers["location"]).query)["scope"][0]


def test_gating_off_asks_only_for_identify():
    assert settings.beta_guild_id == ""
    assert _login_scope() == "identify"


def test_gating_on_also_asks_for_guild_membership(gating_on):
    assert _login_scope() == "identify guilds.members.read"


def test_gating_off_issues_a_token_without_asking_discord_about_membership(monkeypatch, created_users):
    member_calls = _fake_discord(monkeypatch, AssertionError("member endpoint should not be called"))

    result = _login_and_callback()

    assert member_calls == []
    assert result["token"]
    assert result["state"] == "client-state"
    assert created_users == [DISCORD_ID]


def test_member_with_the_beta_role_gets_a_token(monkeypatch, gating_on, created_users):
    member_calls = _fake_discord(monkeypatch, httpx.Response(200, json={"roles": ["999", ROLE_ID]}))

    result = _login_and_callback()

    assert member_calls == ["Bearer discord-access-token"]
    assert result["token"]
    assert "error" not in result
    assert created_users == [DISCORD_ID]


def test_membership_alone_is_enough_when_no_role_is_configured(monkeypatch, gating_on, created_users):
    monkeypatch.setattr(settings, "beta_role_id", "")
    _fake_discord(monkeypatch, httpx.Response(200, json={"roles": []}))

    result = _login_and_callback()

    assert result["token"]


@pytest.mark.parametrize("member_response, expected_error, expected_missing", [
    (httpx.Response(200, json={"roles": ["999"]}), auth.NOT_IN_BETA, auth.MISSING_ROLE),  # member, no beta role
    (httpx.Response(404, json={"message": "Unknown Guild", "code": 10004}), auth.NOT_IN_BETA, auth.MISSING_SERVER),
    (httpx.Response(500), auth.BETA_CHECK_FAILED, None),
    (httpx.Response(429, json={"retry_after": 1}), auth.BETA_CHECK_FAILED, None),
    (httpx.Response(403, json={"message": "Missing Access"}), auth.BETA_CHECK_FAILED, None),
    (httpx.Response(200, text="not json"), auth.BETA_CHECK_FAILED, None),
    (httpx.ConnectTimeout("timed out"), auth.BETA_CHECK_FAILED, None),
])
def test_refused_logins_get_no_token_and_no_user(
    monkeypatch, gating_on, created_users, caplog, member_response, expected_error, expected_missing,
):
    _fake_discord(monkeypatch, member_response)

    with caplog.at_level(logging.WARNING, logger=auth.__name__):
        result = _login_and_callback()

    assert "token" not in result
    assert result["error"] == expected_error
    assert result.get("missing") == expected_missing
    assert result["state"] == "client-state"
    assert result["username"] == "jeff"
    assert created_users == []
    # The waitlist entry.
    assert any(
        "beta sign-in refused" in record.getMessage() and f"discord_id={DISCORD_ID}" in record.getMessage()
        for record in caplog.records
    )


def test_cancel_on_discords_consent_screen_goes_back_to_the_client(monkeypatch, created_users):
    _fake_discord(monkeypatch, AssertionError("no Discord call expected after a cancel"))
    login = client.get(
        "/auth/discord/login",
        params={"redirect_uri": CLIENT_REDIRECT, "client_state": "client-state"},
        follow_redirects=False,
    )
    nonce = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]

    callback = client.get(
        "/auth/discord/callback",
        params={"error": "access_denied", "error_description": "The resource owner denied the request", "state": nonce},
        follow_redirects=False,
    )

    result = {key: values[0] for key, values in parse_qs(urlsplit(callback.headers["location"]).query).items()}
    assert callback.headers["location"].startswith(CLIENT_REDIRECT + "?")
    assert result == {"error": "access_denied", "state": "client-state"}
    assert created_users == []
