"""One-time Discord login for a stable per-pilot identity.

Authorization Code + PKCE, no client secret — ALICE is a distributed desktop app with
nowhere safe to keep one (anyone could pull it out of an installed copy), and Discord's
"Public Client" setting exists for exactly this case. See docs/todo.md's Phase 2 section
for how the resulting identity feeds thread_id/user_id.

Only ever needs the `identify` scope. The goal is a durable Discord user id to key the
trade ledger by, not ongoing API access — so the identity is cached locally after the
first login and the access token itself is discarded immediately after the one /users/@me
call. Nothing here refreshes a token or calls Discord again once the cache is warm.
"""
import base64
import hashlib
import json
import os
import secrets
import webbrowser
from asyncio import get_running_loop
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
IDENTIFY_URL = "https://discord.com/api/users/@me"
DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"

# Not a secret — just a Discord user id + username, cached so login only ever happens
# once per install. Per-user home directory, not the repo, same reasoning as any other
# local machine state.
IDENTITY_CACHE_PATH = Path.home() / ".alice" / "identity.json"


@dataclass
class PilotIdentity:
    user_id: str
    username: str


def _load_cached_identity() -> PilotIdentity | None:
    if not IDENTITY_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(IDENTITY_CACHE_PATH.read_text())
        return PilotIdentity(user_id=data["user_id"], username=data["username"])
    except (json.JSONDecodeError, KeyError):
        # A corrupted cache shouldn't block startup — just log in again and overwrite it.
        return None


def _save_cached_identity(identity: PilotIdentity) -> None:
    IDENTITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_CACHE_PATH.write_text(json.dumps({"user_id": identity.user_id, "username": identity.username}))


def _pkce_pair() -> tuple[str, str]:
    # 86 chars from 64 random bytes — comfortably inside RFC 7636's 43-128 range, and
    # token_urlsafe's alphabet (A-Za-z0-9-_) is already a subset of PKCE's allowed set.
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches exactly one redirect and hands the query params back via server.result —
    an HTTP handler has no return value of its own, so this is the only way to get data
    back up to the caller waiting on handle_request()."""

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        self.server.result = {
            "code": params.get("code", [None])[0],
            "state": params.get("state", [None])[0],
            "error": params.get("error", [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Signed in for ALICE. You can close this tab.</body></html>")

    def log_message(self, format, *args):
        pass  # BaseHTTPRequestHandler logs every request to stderr by default.


def _await_callback(port: int) -> dict:
    server = HTTPServer(("localhost", port), _CallbackHandler)
    server.result = None
    server.handle_request()  # blocks for exactly one request, then returns on its own
    return server.result


async def login() -> PilotIdentity:
    """Runs the full Discord OAuth PKCE flow once and returns the pilot's identity."""
    client_id = os.getenv("DISCORD_CLIENT_ID")
    if not client_id:
        raise ValueError("DISCORD_CLIENT_ID is not set — see .env-template's OAuth section.")
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    port = urlparse(redirect_uri).port

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    authorize_url = f"{AUTHORIZE_URL}?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "scope": "identify",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    webbrowser.open(authorize_url)

    # The wait for the pilot to actually complete the browser flow is unbounded, unlike
    # every other network call in this codebase (which use a fixed timeout) — this has
    # to run off the event loop or it'd stall everything else ALICE is doing.
    result = await get_running_loop().run_in_executor(None, _await_callback, port)

    if result is None or result.get("error") or not result.get("code"):
        raise RuntimeError(f"Discord login failed or was cancelled: {result}")
    if result.get("state") != state:
        raise RuntimeError("Discord login response didn't match the request that started it — aborting.")

    token_response = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]

    identify_response = requests.get(IDENTIFY_URL, headers={"Authorization": f"Bearer {access_token}"})
    identify_response.raise_for_status()
    profile = identify_response.json()

    identity = PilotIdentity(user_id=profile["id"], username=profile["username"])
    _save_cached_identity(identity)
    return identity


async def get_pilot_identity() -> PilotIdentity:
    """The entry point everything else calls — the cached identity if one exists,
    otherwise runs the login flow once and caches the result for next time."""
    cached = _load_cached_identity()
    if cached is not None:
        return cached
    return await login()
