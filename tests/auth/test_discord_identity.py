"""Covers the client-side half of the login relay (see server/routes/auth.py for the
other half): the local session cache, login()'s outcome handling, and get_pilot_session's
cache-then-validate-then-relogin flow — with the network and the one-shot HTTP listener
faked out. Nothing here talks to Discord, the real server, or opens a real port/browser
tab.
"""
import base64
import json
import time

import httpx
import pytest

from auth import discord_identity, login_pages
from auth.session_auth import PilotSessionAuth


def _jwt(expires_in_s: float) -> str:
    """A JWT-shaped token whose payload carries `exp` -- unsigned, which is all the
    client-side expiry check reads."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + expires_in_s}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_cache_round_trips_a_session(tmp_path, monkeypatch):
    cache_path = tmp_path / "identity.json"
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", cache_path)
    session = discord_identity.PilotSession(token="abc.def.ghi", username="jeff")

    discord_identity._save_cached_session(session)
    loaded = discord_identity._load_cached_session()

    assert loaded == session


def test_cache_returns_none_when_nothing_cached_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")

    assert discord_identity._load_cached_session() is None


def test_cache_returns_none_rather_than_raising_on_a_corrupted_file(tmp_path, monkeypatch):
    cache_path = tmp_path / "identity.json"
    cache_path.write_text("not valid json")
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", cache_path)

    assert discord_identity._load_cached_session() is None


async def test_get_pilot_session_returns_the_cache_without_logging_in_again(monkeypatch):
    cached = discord_identity.PilotSession(token=_jwt(5 * 24 * 3600), username="jeff")
    monkeypatch.setattr(discord_identity, "_load_cached_session", lambda: cached)
    monkeypatch.setattr(discord_identity, "_is_valid", _returning(True))

    async def _fail_if_called():
        raise AssertionError("login() should not run when a still-valid cached session exists")

    monkeypatch.setattr(discord_identity, "login", _fail_if_called)

    result = await discord_identity.get_pilot_session()

    assert result is cached


async def test_get_pilot_session_relogs_in_when_the_cached_token_is_no_longer_valid(monkeypatch):
    cached = discord_identity.PilotSession(token="expired", username="jeff")
    fresh = discord_identity.PilotSession(token="new.token", username="jeff")
    monkeypatch.setattr(discord_identity, "_load_cached_session", lambda: cached)
    monkeypatch.setattr(discord_identity, "_is_valid", _returning(False))
    monkeypatch.setattr(discord_identity, "login", _returning(fresh))

    result = await discord_identity.get_pilot_session()

    assert result is fresh


async def test_is_valid_treats_a_200_response_as_valid(monkeypatch):
    session = discord_identity.PilotSession(token="abc", username="jeff")
    _mock_transport(monkeypatch, lambda request: httpx.Response(200, json={"id": "1"}))

    assert await discord_identity._is_valid(session) is True


async def test_is_valid_treats_a_401_response_as_invalid(monkeypatch):
    session = discord_identity.PilotSession(token="abc", username="jeff")
    _mock_transport(monkeypatch, lambda request: httpx.Response(401))

    assert await discord_identity._is_valid(session) is False


async def test_is_valid_lets_a_cached_session_through_when_the_server_is_unreachable(monkeypatch):
    # An outage shouldn't force a re-login the pilot can't complete either — see the
    # function's own docstring.
    session = discord_identity.PilotSession(token="abc", username="jeff")

    def _raise(request):
        raise httpx.ConnectError("connection refused")

    _mock_transport(monkeypatch, _raise)

    assert await discord_identity._is_valid(session) is True


async def test_login_raises_when_the_callback_reports_an_error(monkeypatch):
    monkeypatch.setattr(discord_identity, "webbrowser", _NoOpWebbrowser())
    monkeypatch.setattr(
        discord_identity, "_await_callback",
        lambda port: {"token": None, "username": None, "state": None, "error": "access_denied"},
    )

    with pytest.raises(discord_identity.LoginFailed, match="didn't match"):
        await discord_identity.login()


async def test_login_raises_on_a_state_mismatch(monkeypatch):
    monkeypatch.setattr(discord_identity, "webbrowser", _NoOpWebbrowser())
    monkeypatch.setattr(
        discord_identity, "_await_callback",
        lambda port: {"token": "a.b.c", "username": "jeff", "state": "wrong-state", "error": None},
    )

    with pytest.raises(RuntimeError, match="didn't match"):
        await discord_identity.login()


async def test_login_success_caches_the_session(monkeypatch, tmp_path):
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")

    captured_state = {}

    def fake_await_callback(port):
        # login() generates state itself and only hands it out via the login URL — the
        # fake "callback" has to echo back whatever state login() actually used, the
        # same way the server's real redirect would.
        return {"token": "a.b.c", "username": "jeff", "state": captured_state["state"], "error": None}

    def fake_open(url, *args, **kwargs):
        captured_state["state"] = dict(_query_pairs(url))["client_state"]
        return True

    monkeypatch.setattr(discord_identity, "webbrowser", _NoOpWebbrowser(open_fn=fake_open))
    monkeypatch.setattr(discord_identity, "_await_callback", fake_await_callback)

    session = await discord_identity.login()

    assert session == discord_identity.PilotSession(token="a.b.c", username="jeff")
    assert discord_identity._load_cached_session() == session


def _returning(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


def _mock_transport(monkeypatch, handler):
    # discord_identity._is_valid builds its own httpx.AsyncClient inline (base_url +
    # timeout) rather than through a shared helper — patch the constructor it actually
    # calls so the request never leaves the process.
    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)


class _NoOpWebbrowser:
    def __init__(self, open_fn=None):
        self._open_fn = open_fn or (lambda url, *a, **k: True)

    def open(self, url, *args, **kwargs):
        return self._open_fn(url, *args, **kwargs)


def _query_pairs(url):
    from urllib.parse import parse_qsl, urlparse
    return parse_qsl(urlparse(url).query)


# ---- staying logged in (2026-10-12) ----

def test_token_expiry_is_read_from_the_jwt_payload():
    assert discord_identity.token_expires_within(_jwt(3600), 24 * 3600)
    assert not discord_identity.token_expires_within(_jwt(3 * 24 * 3600), 24 * 3600)
    # Unreadable counts as expiring, so it gets replaced rather than trusted.
    assert discord_identity.token_expires_within("not-a-jwt", 24 * 3600)


async def test_get_pilot_session_renews_a_token_about_to_expire_at_startup(monkeypatch):
    cached = discord_identity.PilotSession(token=_jwt(3600), username="jeff")
    fresh = discord_identity.PilotSession(token=_jwt(7 * 24 * 3600), username="jeff")
    monkeypatch.setattr(discord_identity, "_load_cached_session", lambda: cached)
    monkeypatch.setattr(discord_identity, "_is_valid", _returning(True))
    monkeypatch.setattr(discord_identity, "login", _returning(fresh))

    assert await discord_identity.get_pilot_session() is fresh


async def test_relogin_after_rejection_logs_in_once_for_many_rejected_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")
    discord_identity._save_cached_session(discord_identity.PilotSession(token="old", username="jeff"))
    logins = []

    async def fake_login():
        logins.append(1)
        session = discord_identity.PilotSession(token="new", username="jeff")
        discord_identity._save_cached_session(session)
        return session

    monkeypatch.setattr(discord_identity, "login", fake_login)

    first = await discord_identity.relogin_after_rejection("old")
    # A second request rejected with the same old token, finishing after the first
    # re-login, gets the new token without another browser login.
    second = await discord_identity.relogin_after_rejection("old")

    assert (first, second) == ("new", "new")
    assert logins == [1]


async def test_a_401_triggers_one_relogin_and_the_request_is_retried(monkeypatch):
    tokens = iter(["old"])
    monkeypatch.setattr(discord_identity, "cached_session_token", lambda: next(tokens, "new"))

    async def fake_relogin(rejected):
        assert rejected == "old"
        return "new"

    monkeypatch.setattr(discord_identity, "relogin_after_rejection", fake_relogin)
    seen = []

    def handler(request):
        seen.append((request.headers.get("Authorization"), request.content))
        if request.headers.get("Authorization") == "Bearer old":
            return httpx.Response(401, json={"detail": "Invalid or expired token"})
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=PilotSessionAuth()) as client:
        response = await client.post("http://test/tts", json={"text": "Hello."})

    assert response.status_code == 200
    assert [auth for auth, _ in seen] == ["Bearer old", "Bearer new"]
    # The body was resent intact on the retry.
    assert seen[0][1] == seen[1][1]


def _fake_login_callback(monkeypatch, **result):
    """Makes login()'s listener return `result`, echoing back the state login() put in
    the URL it opened (as the server's real redirect would) unless `state` is given."""
    captured_state = {}

    def fake_open(url, *args, **kwargs):
        captured_state["state"] = dict(_query_pairs(url))["client_state"]
        return True

    def fake_await_callback(port):
        full = {"token": None, "username": None, "state": captured_state["state"], "error": None, "missing": None}
        full.update(result)
        return full

    monkeypatch.setattr(discord_identity, "webbrowser", _NoOpWebbrowser(open_fn=fake_open))
    monkeypatch.setattr(discord_identity, "_await_callback", fake_await_callback)


@pytest.mark.parametrize("error, missing, expected_reason, expected_text", [
    ("not_in_beta", "role", "missing_role", "is in the ALICE Discord, but doesn't have beta access"),
    ("not_in_beta", "server", "not_in_server", "isn't in the ALICE Discord server"),
    ("not_in_beta", None, "not_in_beta", "isn't in the ALICE beta yet"),
    ("beta_check_failed", None, "beta_check_failed", "Discord didn't answer"),
])
async def test_login_raises_login_refused_when_the_server_refuses(
    monkeypatch, tmp_path, error, missing, expected_reason, expected_text,
):
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")
    _fake_login_callback(monkeypatch, username="jeff", error=error, missing=missing)

    with pytest.raises(discord_identity.LoginRefused, match=expected_text) as refused:
        await discord_identity.login()

    assert refused.value.reason == expected_reason
    assert discord_identity._load_cached_session() is None


async def test_login_cancelled_on_discord_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")
    _fake_login_callback(monkeypatch, error="access_denied")

    with pytest.raises(discord_identity.LoginFailed, match="cancelled the Discord sign-in") as failed:
        await discord_identity.login()

    assert not isinstance(failed.value, discord_identity.LoginRefused)
    assert discord_identity._load_cached_session() is None


async def test_a_refusal_with_the_wrong_state_is_not_trusted(monkeypatch):
    _fake_login_callback(monkeypatch, error="not_in_beta", missing="role", state="someone-elses")

    with pytest.raises(discord_identity.LoginFailed, match="didn't match") as failed:
        await discord_identity.login()

    assert not isinstance(failed.value, discord_identity.LoginRefused)


def test_refusal_message_names_the_discord_account():
    refused = discord_identity.LoginRefused(login_pages.OUTCOMES["missing_role"], "jeff")

    assert "This Discord account (jeff)" in str(refused)


@pytest.mark.parametrize("result, expected_key", [
    ({"token": "a.b.c", "error": None}, "signed_in"),
    ({"token": None, "error": "not_in_beta", "missing": "role"}, "missing_role"),
    ({"token": None, "error": "not_in_beta", "missing": "server"}, "not_in_server"),
    ({"token": None, "error": "not_in_beta"}, "not_in_beta"),
    ({"token": None, "error": "beta_check_failed"}, "beta_check_failed"),
    ({"token": None, "error": "access_denied"}, "cancelled"),
    ({"token": None, "error": "server_error"}, "failed"),
    ({"token": None, "error": None}, "failed"),
    (None, "failed"),
])
def test_outcome_for_each_listener_result(result, expected_key):
    assert login_pages.outcome_for(result).key == expected_key


@pytest.mark.parametrize("key", list(login_pages.OUTCOMES))
def test_every_outcome_renders_a_self_contained_page(key, monkeypatch):
    # The invite button is a link to follow, not something the page loads; leave it out
    # so the "http" check below only catches resources.
    monkeypatch.setattr(login_pages, "ALICE_DISCORD_INVITE_URL", None)
    page = login_pages.render_page(login_pages.OUTCOMES[key], "jeff")

    assert page.startswith("<!doctype html>")
    assert login_pages.OUTCOMES[key].title.replace("'", "&#x27;") in page
    if "{account}" in login_pages.OUTCOMES[key].body:
        assert "<strong>jeff</strong>" in page
    # The listener closes after this one response, so nothing may load from elsewhere.
    assert "src=" not in page
    assert "http" not in page


def test_page_escapes_the_discord_username():
    page = login_pages.render_page(login_pages.OUTCOMES["missing_role"], "<script>x</script>")

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_invite_button_only_when_an_invite_is_configured(monkeypatch):
    outcome = login_pages.OUTCOMES["not_in_server"]
    monkeypatch.setattr(login_pages, "ALICE_DISCORD_INVITE_URL", None)
    assert "Join the ALICE Discord" not in login_pages.render_page(outcome, "jeff")

    monkeypatch.setattr(login_pages, "ALICE_DISCORD_INVITE_URL", "https://discord.gg/example")

    assert 'href="https://discord.gg/example"' in login_pages.render_page(outcome, "jeff")
    assert "Join the ALICE Discord" not in login_pages.render_page(login_pages.OUTCOMES["missing_role"], "jeff")


@pytest.mark.parametrize("cached_token, expected, unexpected", [
    (None, "Opening your browser to sign in", "expired"),
    ("old", "Your login has expired", None),
])
async def test_relogin_only_says_expired_when_there_was_a_token(
    monkeypatch, tmp_path, capsys, cached_token, expected, unexpected,
):
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")
    if cached_token is not None:
        discord_identity._save_cached_session(discord_identity.PilotSession(token=cached_token, username="jeff"))
    monkeypatch.setattr(discord_identity, "login", _returning(discord_identity.PilotSession(token="new", username="jeff")))

    await discord_identity.relogin_after_rejection(cached_token)

    printed = capsys.readouterr().out
    assert expected in printed
    if unexpected:
        assert unexpected not in printed
