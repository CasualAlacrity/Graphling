"""One-time Discord login for a stable per-pilot session, relayed through ALICE's own
ledger server (app/server/) rather than talking to Discord directly.

ALICE used to do the whole OAuth PKCE dance itself (public client, no secret — a
desktop app has nowhere safe to keep one). Now that a real server sits in front of the
trade ledger, the server holds the Discord client secret and does the actual exchange
independently — the "verified with Discord" property a client claiming its own identity
can't provide. This module's job shrinks to: open a browser at the server's login
endpoint, and catch the JWT it hands back.

Flow (see server/routes/auth.py for the other half):
1. Start a one-shot local listener (unchanged from the old PKCE flow) and open
   {ALICE_API_URL}/auth/discord/login?redirect_uri=<listener>&client_state=<state> —
   the server's URL, not Discord's.
2. The server does its own exchange with Discord, then redirects the browser back to
   the listener with ?token=<jwt>&username=<name>&state=<state>.
3. Validate state, cache {token, username}, done.

Only ever needs one round trip. The goal is a durable session to key the trade ledger
by, not ongoing interactive use — so the session is cached locally after the first
login and nothing here refreshes it until it expires (server-issued JWTs currently last
7 days — see server/config.py's jwt_expire_minutes).
"""
import json
import os
import secrets
import webbrowser
from asyncio import get_running_loop
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"

# Not a secret store — just a JWT + display name, cached so login only ever happens once
# per install (until the token expires). Per-user home directory, not the repo, same
# reasoning as any other local machine state.
IDENTITY_CACHE_PATH = Path.home() / ".alice" / "identity.json"


@dataclass
class PilotSession:
    token: str
    username: str


def _api_url() -> str:
    return os.getenv("ALICE_API_URL", "http://localhost:8000").rstrip("/")


def _load_cached_session() -> PilotSession | None:
    if not IDENTITY_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(IDENTITY_CACHE_PATH.read_text())
        return PilotSession(token=data["token"], username=data["username"])
    except (json.JSONDecodeError, KeyError):
        # A corrupted cache shouldn't block startup — just log in again and overwrite it.
        return None


def _save_cached_session(session: PilotSession) -> None:
    IDENTITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_CACHE_PATH.write_text(json.dumps({"token": session.token, "username": session.username}))


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches exactly one redirect and hands the query params back via server.result —
    an HTTP handler has no return value of its own, so this is the only way to get data
    back up to the caller waiting on handle_request()."""

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        self.server.result = {
            "token": params.get("token", [None])[0],
            "username": params.get("username", [None])[0],
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


async def login() -> PilotSession:
    """Opens ALICE's server-side login endpoint and returns the resulting session."""
    redirect_uri = os.getenv("DISCORD_LOCAL_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    port = urlparse(redirect_uri).port

    state = secrets.token_urlsafe(16)
    login_url = f"{_api_url()}/auth/discord/login?" + urlencode({
        "redirect_uri": redirect_uri,
        "client_state": state,
    })
    webbrowser.open(login_url)

    # The wait for the pilot to actually complete the browser flow is unbounded, unlike
    # every other network call in this codebase (which use a fixed timeout) — this has
    # to run off the event loop or it'd stall everything else ALICE is doing.
    result = await get_running_loop().run_in_executor(None, _await_callback, port)

    if result is None or result.get("error") or not result.get("token"):
        raise RuntimeError(f"ALICE login failed or was cancelled: {result}")
    if result.get("state") != state:
        raise RuntimeError("Login response didn't match the request that started it — aborting.")

    session = PilotSession(token=result["token"], username=result.get("username") or "pilot")
    _save_cached_session(session)
    return session


async def _is_valid(session: PilotSession) -> bool:
    try:
        async with httpx.AsyncClient(base_url=_api_url(), timeout=10) as client:
            response = await client.get("/auth/users/me", headers={"Authorization": f"Bearer {session.token}"})
        return response.status_code == 200
    except httpx.HTTPError:
        # Server unreachable shouldn't force a re-login the pilot can't complete either —
        # let the cached (possibly still-good) session through and fail later if it's
        # actually expired, rather than compounding one outage into a second one.
        return True


async def get_pilot_session() -> PilotSession:
    """The entry point everything else calls — the cached session if one exists and
    still validates against the server, otherwise runs the login flow once and caches
    the result for next time. Checked here rather than left to fail on the first real
    ledger call, so an expired token surfaces as one clear re-login instead of a
    confusing 401 from whatever tool the pilot happened to use first."""
    cached = _load_cached_session()
    if cached is not None and await _is_valid(cached):
        return cached
    return await login()
