"""What ALICE says out loud when a turn fails, instead of going silent (a failed turn used
to print a traceback to the console and nothing else, so the pilot just waited).

Kept apart from the voice loop, which pulls in the microphone and speech model, so the
wording can be tested on its own. Each line says the request did not go through and never
implies anything was recorded -- the same rule as tools/uplink_tool.py's _NOT_DONE.
"""
import httpx

UNREACHABLE = "I can't reach my server right now, so that didn't go through. Try again in a moment."
LOGIN_REJECTED = "My server didn't accept your login, so that didn't go through. You may need to sign in again."
BUSY = "My server is busy right now, so that didn't go through. Try again in a moment."
GENERIC = "Something went wrong on my end, and that didn't go through. Say it again."


def failure_line(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return LOGIN_REJECTED
        if status in (429, 502, 503, 504):
            return BUSY
        return GENERIC
    if isinstance(error, httpx.TransportError):  # includes timeouts
        return UNREACHABLE
    return GENERIC
