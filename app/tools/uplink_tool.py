import logging
from collections.abc import Awaitable
from typing import Any

from langchain_core.tools import BaseTool

from tools.uexcorp.client import UEXCorpClient

logger = logging.getLogger(__name__)


class UnresolvedName(Exception):
    """A name the pilot said (a location filter, a ship) that didn't resolve. str() is
    the pilot-facing message from resolve_or_hedge. Raised from deep inside a lookup
    (filter_by_location) so every tool doesn't have to thread a return value through;
    _safe_run turns it back into that message instead of "UEX is unavailable"."""


class UplinkTool(BaseTool):
    progress_label: str

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{type(self).__name__} only supports async execution — use _arun.")

    async def _safe_run(self, lookup: Awaitable) -> Any:
        try:
            return await lookup
        except UnresolvedName as unresolved:
            return str(unresolved)
        except Exception:
            logger.exception(f"{self.name} failed")
            return ("The UEX pricing API is temporarily unavailable. Tell the user their request couldn't be "
                    "completed and suggest trying again shortly.")


class UEXBackedTool(UplinkTool):
    client: UEXCorpClient
