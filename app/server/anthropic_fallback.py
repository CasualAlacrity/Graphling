"""The gateway's Anthropic fallback -- translates an Ollama /api/chat request (what the
client's ChatOllama sends) into a Claude Messages API call, and the reply back into one
Ollama-shaped done:true chunk. server/llm_gateway_service.py decides WHEN to fall back;
this module only does the translation and the call.

Kept separate from the OpenAI translation in llm_gateway_service.py: the two wire
formats differ enough (system prompt as a top-level field, tool calls/results as
content blocks, prompt caching via cache_control) that sharing code would hide more
than it saves.
"""
import json
from datetime import UTC, datetime

from anthropic import AsyncAnthropic

from model_pricing import MODEL_PRICING
from server.config import settings
from server.tool_call_ids import with_tool_call_ids

ANTHROPIC = "anthropic"
# A spoken ALICE reply is a few sentences and classify is a tiny JSON object; this is
# headroom, not a target. A request's own num_predict wins when it sets one.
DEFAULT_MAX_TOKENS = 4096
TIMEOUT_S = 60.0

_client: AsyncAnthropic | None = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        # One retry: a fallback is already the slow path -- the SDK's default of two
        # retries can stack another ~minute onto a turn the pilot is waiting on.
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=TIMEOUT_S, max_retries=1)
    return _client


def _closed_objects(schema: dict) -> dict:
    """Copy of a JSON schema with additionalProperties: false on every object, which
    structured outputs requires. Pydantic's generated schemas leave it unset."""
    if not isinstance(schema, dict):
        return schema
    closed = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            closed[key] = _closed_objects(value)
        elif isinstance(value, list):
            closed[key] = [_closed_objects(item) for item in value]
        else:
            closed[key] = value
    if closed.get("type") == "object":
        closed.setdefault("additionalProperties", False)
    return closed


def _tool_arguments(arguments) -> dict:
    if isinstance(arguments, dict):
        return arguments
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}


def anthropic_request(payload: dict, model: str) -> dict:
    """Ollama /api/chat request -> keyword arguments for messages.create().

    - Every system message becomes the top-level system prompt, with a cache breakpoint
      on it. Tools render before system, so that one breakpoint caches every tool
      schema plus the persona (~11.5k tokens, identical on every call).
    - Assistant tool calls become tool_use blocks; tool messages become tool_result
      blocks in a user turn, paired by position (server/tool_call_ids.py -- the ollama
      client strips ids, so none arrive).
    - Consecutive same-role messages are merged (parallel tool results must share one
      user message).
    - Sampling options are dropped: ALICE doesn't set any, and newer models reject
      non-default values.
    """
    system_parts = []
    messages: list[dict] = []

    def append(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": blocks})

    for message in with_tool_call_ids(payload.get("messages", [])):
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            if content:
                system_parts.append(content)
        elif role == "tool":
            tool_result = {"type": "tool_result", "tool_use_id": message.get("tool_call_id", ""), "content": content}
            append("user", [tool_result])
        elif role == "assistant":
            # "thinking" is dropped -- Claude never sees the primary model's reasoning.
            blocks = [{"type": "text", "text": content}] if content else []
            for tool_call in message.get("tool_calls") or []:
                blocks.append({
                    "type": "tool_use",
                    "id": tool_call.get("id") or "call",
                    "name": tool_call["function"]["name"],
                    "input": _tool_arguments(tool_call["function"].get("arguments")),
                })
            append("assistant", blocks)
        elif content:
            append("user", [{"type": "text", "text": content}])

    options = payload.get("options") or {}
    request: dict = {
        "model": model,
        "max_tokens": options.get("num_predict") or DEFAULT_MAX_TOKENS,
        "messages": messages,
    }
    if system_parts:
        request["system"] = [{
            "type": "text",
            "text": "\n\n".join(system_parts),
            "cache_control": {"type": "ephemeral"},
        }]

    tools = []
    for tool in payload.get("tools") or []:
        function = tool.get("function", tool)
        tools.append({
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
        })
    if tools:
        request["tools"] = tools

    response_format = payload.get("format")
    if isinstance(response_format, dict):
        request["output_config"] = {"format": {"type": "json_schema", "schema": _closed_objects(response_format)}}
    return request


def ollama_final_chunk(message, model: str) -> dict:
    """Claude Message -> one Ollama-shaped done:true chunk. prompt_eval_count is the
    whole prompt (uncached + cache read + cache write), matching what Ollama reports."""
    text_parts = []
    tool_calls = []
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append({"function": {"name": block.name, "arguments": block.input}})

    ollama_message: dict = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        ollama_message["tool_calls"] = tool_calls

    usage = message.usage
    prompt_tokens = (
        usage.input_tokens + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
    )
    return {
        "model": model,
        "created_at": datetime.now(UTC).isoformat(),
        "message": ollama_message,
        "done": True,
        "done_reason": message.stop_reason or "stop",
        "prompt_eval_count": prompt_tokens,
        "eval_count": usage.output_tokens,
        "alice_backend": ANTHROPIC,
    }


def cost_usd(model: str, usage) -> float | None:
    """5-minute cache writes only -- the breakpoint above never sets a 1h TTL."""
    rates = MODEL_PRICING.get((ANTHROPIC, model))
    if rates is None:
        return None
    dollars = 0.0
    dollars += usage.input_tokens * rates.input
    dollars += (usage.cache_read_input_tokens or 0) * rates.cache_read
    dollars += (usage.cache_creation_input_tokens or 0) * rates.cache_write_5m
    dollars += usage.output_tokens * rates.output
    return dollars / 1_000_000


async def create(payload: dict, model: str):
    return await client().messages.create(**anthropic_request(payload, model))
