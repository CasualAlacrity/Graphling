"""httpx auth for every call to ALICE's own server: the ledger, UEX and wiki caches
(api_client.py), the LLM gateway (llm.py) and the TTS gateway (voice/tts.py). Attaches
the pilot's JWT, read from the login cache at send time, and on a 401 logs in again once
and retries the request (auth.discord_identity.relogin_after_rejection)."""
import httpx


def _cached_token() -> str | None:
    # Imported here, not at module level -- auth.discord_identity has no reason to load
    # for the ollama/openai/anthropic providers, which never construct this.
    from auth.discord_identity import cached_session_token

    return cached_session_token()


class PilotSessionAuth(httpx.Auth):
    """Read per request rather than baked in when a client is built: graph.py builds its
    LLMs at import, and on a first install that happens while the pilot is still
    mid-login (voice.run gathers get_pilot_session alongside everything else)."""

    # The retry after a re-login resends the same request, so its body must be kept.
    requires_request_body = True

    def auth_flow(self, request):
        # Sync clients: attach only. Every client of ALICE's server is async.
        token = _cached_token()
        if token is not None:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request

    async def async_auth_flow(self, request):
        from auth.discord_identity import relogin_after_rejection

        token = _cached_token()
        if token is not None:
            request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code != 401:
            return

        new_token = await relogin_after_rejection(token)
        request.headers["Authorization"] = f"Bearer {new_token}"
        yield request
