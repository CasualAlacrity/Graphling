"""httpx auth for calls to ALICE's own server (the LLM gateway, the TTS gateway): attaches
the pilot's JWT per request, read from the login cache at send time."""
import httpx


class PilotSessionAuth(httpx.Auth):
    """It can't be baked in when the client is built: graph.py builds its LLMs at import,
    and on a first install that happens while the pilot is still mid-login (voice.run
    gathers get_pilot_session alongside everything else)."""

    def auth_flow(self, request):
        # Imported here, not at module level -- auth.discord_identity has no reason to
        # load for the ollama/openai/anthropic providers.
        from auth.discord_identity import cached_session_token

        token = cached_session_token()
        if token is not None:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request
