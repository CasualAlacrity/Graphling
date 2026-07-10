from langchain_core.tools import BaseTool


class UplinkTool(BaseTool):
    progress_label: str
