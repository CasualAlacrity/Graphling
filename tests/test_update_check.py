"""Covers update_check.py (reading the newest version off the stable link's redirect)
and bundle.app_version (ALICE's own version, from pyproject.toml). The download server
is faked with httpx.MockTransport — no network."""
import tomllib
from pathlib import Path

import httpx
import pytest

import bundle
import update_check


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_version_comes_from_the_redirect_target():
    assert update_check.version_from_location("https://heyalice.help/download/ALICE-Setup-0.2.3.exe") == "0.2.3"
    assert update_check.version_from_location("/download/ALICE-Setup-1.10.0.exe") == "1.10.0"
    assert update_check.version_from_location("https://heyalice.help/") is None


@pytest.mark.parametrize(("candidate", "current", "newer"), [
    ("0.2.3", "0.2.2", True),
    ("0.2.10", "0.2.9", True),   # numeric, not alphabetical
    ("0.3", "0.3.0", False),     # same version
    ("0.2.2", "0.2.2", False),
    ("0.2.1", "0.2.2", False),   # never "update" backwards
])
def test_is_newer(candidate, current, newer):
    assert update_check.is_newer(candidate, current) is newer


async def test_reads_the_redirect_without_following_it():
    requested = []

    def handler(request):
        requested.append(request.method)
        return httpx.Response(302, headers={"location": "https://heyalice.help/download/ALICE-Setup-0.2.3.exe"})

    async with _client(handler) as client:
        assert await update_check.latest_version(client) == "0.2.3"
    assert requested == ["HEAD"]  # one header-only request, nothing downloaded


async def test_no_redirect_means_unknown():
    async with _client(lambda request: httpx.Response(404)) as client:
        assert await update_check.latest_version(client) is None


async def test_network_failure_means_unknown():
    def handler(request):
        raise httpx.ConnectError("offline")

    async with _client(handler) as client:
        assert await update_check.latest_version(client) is None


async def test_available_update_only_for_a_newer_version(monkeypatch):
    async def _latest():
        return "0.2.3"

    monkeypatch.setattr(update_check, "latest_version", _latest)
    assert await update_check.available_update("0.2.2") == "0.2.3"
    assert await update_check.available_update("0.2.3") is None
    assert await update_check.available_update(None) is None


def test_app_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert bundle.app_version() == expected
