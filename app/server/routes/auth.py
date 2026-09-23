"""Discord OAuth relay for a desktop client.

ALICE isn't a browser tab, so it can't just sit at this server's origin and pick up a
session cookie once Discord's redirect lands here. Instead the desktop client runs its
own one-shot local HTTP listener (see auth/discord_identity.py) and this server bounces
the browser back to it once the real Discord exchange — the part that needs the client
secret only a server can hold — is done:

1. Client opens GET /discord/login?redirect_uri=<its localhost listener>&client_state=<x>
2. This route stashes {redirect_uri, client_state} behind a fresh nonce and redirects to
   Discord's own authorize URL, with state=<nonce> and OUR OWN registered redirect_uri.
3. Discord redirects back to GET /discord/callback?code&state=<nonce>. We look up the
   nonce, exchange the code for a Discord token WITH the client secret (the actual
   "verified with Discord" property — a client can't fake this), fetch the profile,
   resolve/create the local user, issue our own JWT.
4. We redirect the browser once more, to the STASHED client redirect_uri:
   <redirect_uri>?token=<jwt>&state=<client_state> — completing the loop back to the
   desktop app's own listener.

_PENDING_LOGINS is in-memory and unbounded-until-consumed, which is fine at pilot-count
scale (single process, a handful of users) — revisit if this ever needs to survive a
restart or run behind multiple server processes.
"""
import secrets
import time
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from jose import jwt

from db.models import User
from db.user_service import get_or_create_user
from server.config import settings
from server.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL = "https://discord.com/api/users/@me"

_PENDING_LOGIN_TTL_SECONDS = 600
_PENDING_LOGINS: dict[str, dict] = {}


def _stash_pending_login(redirect_uri: str, client_state: str) -> str:
    now = time.monotonic()
    for nonce, entry in list(_PENDING_LOGINS.items()):
        if now - entry["created_at"] > _PENDING_LOGIN_TTL_SECONDS:
            del _PENDING_LOGINS[nonce]

    nonce = secrets.token_urlsafe(24)
    _PENDING_LOGINS[nonce] = {"redirect_uri": redirect_uri, "client_state": client_state, "created_at": now}
    return nonce


def _issue_jwt(user_id) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub": str(user_id), "exp": expire}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@router.get("/discord/login")
async def discord_login(redirect_uri: str, client_state: str) -> RedirectResponse:
    nonce = _stash_pending_login(redirect_uri, client_state)
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": nonce,
    }
    return RedirectResponse(f"{DISCORD_AUTHORIZE_URL}?{httpx.QueryParams(params)}")


@router.get("/discord/callback")
async def discord_callback(code: str, state: str) -> RedirectResponse:
    pending = _PENDING_LOGINS.pop(state, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="Login session expired or unrecognized — try logging in again.")

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        profile_response = await client.get(DISCORD_USER_URL, headers={"Authorization": f"Bearer {access_token}"})
        profile_response.raise_for_status()
        profile = profile_response.json()

    user = await get_or_create_user(str(profile["id"]))
    token = _issue_jwt(user.id)

    # username isn't stored on User at all — it's Discord's display data, not identity,
    # and the only thing that ever reads it is the client's own "Signed in as X" print
    # (voice/__init__.py). Passed through the redirect instead of persisted so User stays
    # just the identity FK everything else hangs off, not a profile cache to keep fresh.
    callback_params = httpx.QueryParams({
        "token": token, "state": pending["client_state"], "username": profile["username"],
    })
    return RedirectResponse(f"{pending['redirect_uri']}?{callback_params}")


@router.get("/users/me")
async def get_me(user: User = Depends(get_current_user)):
    return {"id": str(user.id), "discord_id": user.discord_id}
