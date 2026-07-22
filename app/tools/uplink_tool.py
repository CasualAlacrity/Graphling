import logging
from collections.abc import Awaitable
from typing import Any

from langchain_core.tools import BaseTool

from tools.uexcorp.client import UEXCorpClient

logger = logging.getLogger(__name__)


class UplinkTool(BaseTool):
    progress_label: str

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{type(self).__name__} only supports async execution — use _arun.")

    async def _safe_run(self, lookup: Awaitable) -> Any:
        try:
            return await lookup
        except Exception:
            logger.exception(f"{self.name} failed")
            return ("The UEX pricing API is temporarily unavailable. Tell the user their request couldn't be "
                    "completed and suggest trying again shortly.")


class UEXBackedTool(UplinkTool):
    client: UEXCorpClient
