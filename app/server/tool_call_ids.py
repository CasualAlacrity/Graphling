"""Gives tool calls and tool results matching ids, for the gateway's fallback translations.

Ollama's /api/chat pairs tool results with tool calls by ORDER, and the ollama Python
client (what ChatOllama sends through) drops any id on the way out: its Message model has
no tool_call_id field and its ToolCall has no id. So the gateway receives tool calls and
tool results with no ids at all -- verified 2026-10-09, when the first Anthropic fallback
turn that used a tool came back 400 on an empty tool_use_id.

Claude and OpenAI both require explicit pairing, so before translating, every assistant
tool call gets an id and each following tool message takes the next one in order -- the
same pairing Ollama itself assumes.
"""
import copy


def with_tool_call_ids(messages: list[dict]) -> list[dict]:
    """A copy of `messages` where every assistant tool call has an "id" and every tool
    message has the matching "tool_call_id". Ids already present are kept."""
    paired = []
    pending_ids: list[str] = []
    for index, message in enumerate(messages):
        message = copy.deepcopy(message)
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            pending_ids = []
            for position, tool_call in enumerate(message["tool_calls"]):
                tool_call_id = tool_call.get("id") or f"call_{index}_{position}"
                tool_call["id"] = tool_call_id
                pending_ids.append(tool_call_id)
        elif role == "tool":
            if not message.get("tool_call_id") and pending_ids:
                message["tool_call_id"] = pending_ids[0]
            if message.get("tool_call_id") in pending_ids:
                pending_ids.remove(message["tool_call_id"])
        paired.append(message)
    return paired
