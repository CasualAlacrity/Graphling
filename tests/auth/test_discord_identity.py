"""Covers the Discord PKCE login flow's own logic (PKCE pair shape, the local identity
cache, and login()'s outcome handling) with the network and the one-shot HTTP listener
faked out — nothing here talks to Discord or opens a real port or browser tab.
"""
import base64
import hashlib
import json

import pytest

from auth import discord_identity


class _FakeResponse:
    def __init__(self, json_data, status_ok=True):
        self._json_data = json_data
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("non-2xx response")

    def json(self):
        return self._json_data


def test_pkce_pair_verifier_is_a_valid_length():
    verifier, _ = discord_identity._pkce_pair()
    assert 43 <= len(verifier) <= 128


def test_pkce_pair_challenge_is_sha256_of_verifier():
    verifier, challenge = discord_identity._pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_cache_round_trips_an_identity(tmp_path, monkeypatch):
    cache_path = tmp_path / "identity.json"
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", cache_path)
    identity = discord_identity.PilotIdentity(user_id="123", username="jeff")

    discord_identity._save_cached_identity(identity)
    loaded = discord_identity._load_cached_identity()

    assert loaded == identity


def test_cache_returns_none_when_nothing_cached_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")

    assert discord_identity._load_cached_identity() is None


def test_cache_returns_none_rather_than_raising_on_a_corrupted_file(tmp_path, monkeypatch):
    cache_path = tmp_path / "identity.json"
    cache_path.write_text("not valid json")
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", cache_path)

    assert discord_identity._load_cached_identity() is None


async def test_get_pilot_identity_returns_the_cache_without_logging_in_again(monkeypatch):
    cached = discord_identity.PilotIdentity(user_id="123", username="jeff")
    monkeypatch.setattr(discord_identity, "_load_cached_identity", lambda: cached)

    async def _fail_if_called():
        raise AssertionError("login() should not run when a cached identity exists")

    monkeypatch.setattr(discord_identity, "login", _fail_if_called)

    result = await discord_identity.get_pilot_identity()

    assert result is cached


async def test_login_raises_without_a_client_id(monkeypatch):
    monkeypatch.delenv("DISCORD_CLIENT_ID", raising=False)

    with pytest.raises(ValueError, match="DISCORD_CLIENT_ID"):
        await discord_identity.login()


async def test_login_raises_on_a_state_mismatch(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_ID", "test-client")
    monkeypatch.setattr(discord_identity, "webbrowser", _NoOpWebbrowser())
    monkeypatch.setattr(
        discord_identity, "_await_callback",
        lambda port: {"code": "abc", "state": "wrong-state", "error": None},
    )

    with pytest.raises(RuntimeError, match="didn't match"):
        await discord_identity.login()


async def test_login_raises_when_discord_reports_an_error(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_ID", "test-client")
    monkeypatch.setattr(discord_identity, "webbrowser", _NoOpWebbrowser())
    monkeypatch.setattr(
        discord_identity, "_await_callback",
        lambda port: {"code": None, "state": None, "error": "access_denied"},
    )

    with pytest.raises(RuntimeError, match="failed or was cancelled"):
        await discord_identity.login()


async def test_login_success_exchanges_code_and_caches_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_CLIENT_ID", "test-client")
    monkeypatch.setattr(discord_identity, "IDENTITY_CACHE_PATH", tmp_path / "identity.json")

    captured_state = {}

    def fake_await_callback(port):
        # login() generates state itself and only hands it out via the authorize URL —
        # the fake "callback" has to echo back whatever state login() actually used, the
        # same way a real Discord redirect would.
        return {"code": "the-code", "state": captured_state["state"], "error": None}

    def fake_open(url, *args, **kwargs):
        captured_state["state"] = dict(_query_pairs(url))["state"]
        return True

    monkeypatch.setattr(discord_identity, "webbrowser", _NoOpWebbrowser(open_fn=fake_open))
    monkeypatch.setattr(discord_identity, "_await_callback", fake_await_callback)
    monkeypatch.setattr(
        discord_identity.requests, "post",
        lambda *a, **k: _FakeResponse({"access_token": "fake-token"}),
    )
    monkeypatch.setattr(
        discord_identity.requests, "get",
        lambda *a, **k: _FakeResponse({"id": "999", "username": "jeff"}),
    )

    identity = await discord_identity.login()

    assert identity == discord_identity.PilotIdentity(user_id="999", username="jeff")
    assert discord_identity._load_cached_identity() == identity


class _NoOpWebbrowser:
    def __init__(self, open_fn=None):
        self._open_fn = open_fn or (lambda url, *a, **k: True)

    def open(self, url, *args, **kwargs):
        return self._open_fn(url, *args, **kwargs)


def _query_pairs(url):
    from urllib.parse import parse_qsl, urlparse
    return parse_qsl(urlparse(url).query)
