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
3. Validate state, cache {token, username}, done. While the beta is gated
   (server/config.py's beta_guild_id), the server may instead send ?error=not_in_beta or
   ?error=beta_check_failed — login() raises LoginRefused with a message for the pilot.

Only ever needs one round trip. The goal is a durable session to key the trade ledger
by, not ongoing interactive use — so the session is cached locally after the first
login. Server-issued JWTs last 7 days (server/config.py's jwt_expire_minutes), and two
things keep a pilot from ever hitting the wall mid-flight:
- get_pilot_session (at startup) logs in again if the token expires within
  REFRESH_WITHIN, so the browser opens while the pilot is launching ALICE, not in the
  middle of a run;
- if a request is still rejected (401) later, auth/session_auth.py calls
  relogin_after_rejection, which opens one login no matter how many requests failed
  at once, and retries.
"""
import asyncio
import base64
import html
import json
import os
import secrets
import threading
import time
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

# At startup, a token with less than this left is renewed straight away rather than left
# to expire during a session.
REFRESH_WITHIN_S = 24 * 3600

# One login at a time: classify, respond and a TTS call can all get a 401 together, and
# each opening its own browser tab would be a mess. A threading lock, not asyncio's: the
# overlay (qasync) and voice (its own thread) run separate event loops, and an
# asyncio.Lock is bound to one of them.
_relogin_lock = threading.Lock()


# The server's refusal reasons (server/routes/auth.py's NOT_IN_BETA / BETA_CHECK_FAILED),
# worded for the pilot. "{account}" is the Discord name they signed in with, since a
# second Discord account is the likeliest reason a real beta pilot gets refused.
_REFUSAL_MESSAGES = {
    "not_in_beta": "This Discord account{account} isn't in the ALICE beta yet. "
                   "Ask in the ALICE Discord server for beta access, then start ALICE again.",
    "beta_check_failed": "Discord didn't answer when ALICE checked your beta access. "
                         "This is usually brief; start ALICE again in a minute.",
}


class LoginRefused(RuntimeError):
    """The server completed the Discord login but refused to issue a token (beta gating).
    str() is the pilot-facing message."""

    def __init__(self, reason: str, username: str | None):
        self.reason = reason
        account = f" ({username})" if username else ""
        super().__init__(_REFUSAL_MESSAGES[reason].format(account=account))


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


def cached_session_token() -> str | None:
    """The cached JWT without validating it or prompting a login -- for code that runs
    per request and can't block on a browser round trip (llm.py's gateway auth). An
    expired token just gets a 401 from the server, same as any other request."""
    session = _load_cached_session()
    return session.token if session is not None else None


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
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_callback_page(self.server.result).encode())

    def log_message(self, format, *args):
        pass  # BaseHTTPRequestHandler logs every request to stderr by default.


def _callback_page(result: dict) -> str:
    error = result["error"]
    if error in _REFUSAL_MESSAGES:
        message = str(LoginRefused(error, result["username"]))
    elif error or not result["token"]:
        message = "ALICE sign-in didn't complete. Close this tab and start ALICE again."
    else:
        message = "Signed in for ALICE. You can close this tab."
    return f"<html><body>{html.escape(message)}</body></html>"


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

    if result is not None and result.get("error") in _REFUSAL_MESSAGES and result.get("state") == state:
        raise LoginRefused(result["error"], result.get("username"))
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


def token_expires_within(token: str, seconds: float) -> bool:
    """Whether the JWT's own `exp` is less than `seconds` away. Reads the payload without
    verifying it — the server does that; this only decides when to renew. An unreadable
    token counts as expiring, so it gets replaced."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        expires_at = json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except (IndexError, ValueError, KeyError, TypeError):
        return True
    return expires_at - time.time() < seconds


async def get_pilot_session() -> PilotSession:
    """Run once at startup (voice.run): the cached session if one exists, still validates
    against the server, and isn't about to expire — otherwise logs in now and caches the
    result. Checked up front so an expired or nearly-expired token surfaces as one clear
    login while the pilot is launching ALICE, instead of a 401 from whatever tool the
    pilot happened to use first."""
    cached = _load_cached_session()
    if cached is not None and not token_expires_within(cached.token, REFRESH_WITHIN_S) and await _is_valid(cached):
        return cached
    return await login()


async def relogin_after_rejection(rejected_token: str | None) -> str:
    """The server said 401 to `rejected_token`. Logs in again and returns the new token —
    unless another request already did that while this one waited for the lock, in which
    case the fresh token is returned without a second login."""
    await asyncio.to_thread(_relogin_lock.acquire)
    try:
        cached = _load_cached_session()
        if cached is not None and cached.token != rejected_token:
            return cached.token
        print("[ALICE] Your login has expired. Opening your browser to sign in with Discord again...", flush=True)
        session = await login()
        return session.token
    finally:
        _relogin_lock.release()
