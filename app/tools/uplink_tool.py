import logging
from collections.abc import Awaitable
from typing import Any

import httpx
from langchain_core.tools import BaseTool

from tools.uexcorp.client import UEXCorpClient

logger = logging.getLogger(__name__)


class UnresolvedName(Exception):
    """A name the pilot said (a location filter, a ship) that didn't resolve. str() is
    the pilot-facing message from resolve_or_hedge. Raised from deep inside a lookup
    (filter_by_location) so every tool doesn't have to thread a return value through;
    _safe_run turns it back into that message instead of "UEX is unavailable"."""


# Every failure ends the same way for the pilot: it didn't happen. Said explicitly because
# with a vaguer message the model claimed a blocked delete had worked ("The current trade
# run has been marked for termination", response matrix row 80, 2026-10-16).
_NOT_DONE = (" Tell the pilot it didn't go through, and don't say or imply that anything was found, "
             "recorded or changed.")


def failure_message(tool_name: str, error: Exception) -> str:
    """What a tool tells the model when it fails, saying what actually failed. It used to
    be "The UEX pricing API is temporarily unavailable" for every exception, so a ledger
    error or a bug was reported to pilots as a UEX outage (rows 52-54, starting a run)."""
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        detail = ""
        try:
            detail = error.response.json().get("detail", "")
        except ValueError:
            pass
        if status == 503:
            return f"ALICE's live data is unavailable right now ({detail or 'service unavailable'})." + _NOT_DONE
        if status in (401, 403):
            return "ALICE's server didn't accept the pilot's login; they may need to log in again." + _NOT_DONE
        return f"ALICE's server answered with an error ({status})." + _NOT_DONE
    if isinstance(error, httpx.TimeoutException):
        return "ALICE's server took too long to answer." + _NOT_DONE
    if isinstance(error, httpx.TransportError):
        return "ALICE couldn't reach her server; the connection may be down." + _NOT_DONE
    return f"Something went wrong inside ALICE while running {tool_name}." + _NOT_DONE


class UplinkTool(BaseTool):
    progress_label: str

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{type(self).__name__} only supports async execution — use _arun.")

    async def _safe_run(self, lookup: Awaitable) -> Any:
        try:
            return await lookup
        except UnresolvedName as unresolved:
            return str(unresolved)
        except Exception as error:
            logger.exception(f"{self.name} failed")
            return failure_message(self.name, error)


class UEXBackedTool(UplinkTool):
    client: UEXCorpClient
