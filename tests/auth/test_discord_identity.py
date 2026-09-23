"""Covers the client-side half of the login relay (see server/routes/auth.py for the
other half): the local session cache, login()'s outcome handling, and get_pilot_session's
cache-then-validate-then-relogin flow — with the network and the one-shot HTTP listener
faked out. Nothing here talks to Discord, the real server, or opens a real port/browser
tab.
"""
import httpx
import pytest

from auth import discord_identity


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
    cached = discord_identity.PilotSession(token="abc.def.ghi", username="jeff")
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

    with pytest.raises(RuntimeError, match="failed or was cancelled"):
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
