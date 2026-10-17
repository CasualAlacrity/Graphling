"""Repairs for two ways the chat model's reply goes wrong, found by the response-matrix
baseline (5 runs, 2026-10-17; docs/todo.md 6c):

- A tool call written out as text instead of made: gemma4 sometimes answers
  `commodity_price_lookup{commodity:<|"|>Laranite<|"|>}` (its own call format) or
  `mark_cargo_sold(commodity="Copper", quantity_scu=640)` as the reply's content. Nothing
  runs, and the pilot would hear the call read aloud. recover_tool_call turns it back into
  a real call when the name is one of ALICE's tools.
- An empty reply: the model thinks, then stops without a word (after a timer was set, for
  instance), so the pilot hears nothing. graph.respond retries once and then falls back to
  fallback_line.
"""
import ast
import re
import uuid

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

# A whole reply that is just `name(...)` or `name{...}`.
_CALL = re.compile(r"^\s*(?P<name>[a-z_][a-z0-9_]*)\s*(?P<open>[({])(?P<args>.*)[)}]\s*$", re.DOTALL)
# gemma4's own argument format: key:<|"|>text<|"|> or key:123.
_GEMMA_ARG = re.compile(r'(\w+)\s*:\s*(?:<\|"\|>(.*?)<\|"\|>|([^,}]+))', re.DOTALL)

# A tool's own result is spoken as the fallback only when it's this short: a timer's
# "Timer 'Loading' set for 15:00.", not a page of route data.
MAX_SPOKEN_TOOL_RESULT = 200
NOTHING_TO_SAY = "Sorry, I lost my train of thought there. Say that again?"


def _text(message: AIMessage) -> str:
    return message.content if isinstance(message.content, str) else ""


def _python_style_args(args: str) -> dict | None:
    try:
        call = ast.parse(f"f({args})", mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call) or call.args:
        return None
    parsed = {}
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        try:
            parsed[keyword.arg] = ast.literal_eval(keyword.value)
        except ValueError:
            return None
    return parsed


def _gemma_style_args(args: str) -> dict:
    parsed = {}
    for key, quoted, bare in _GEMMA_ARG.findall(args):
        if quoted:
            parsed[key] = quoted
            continue
        value = bare.strip()
        try:
            parsed[key] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            parsed[key] = value
    return parsed


def recover_tool_call(response: AIMessage, tool_names: set[str]) -> AIMessage:
    """The response, with a tool call written out as its text turned into a real call.
    Anything else comes back unchanged."""
    if response.tool_calls:
        return response
    match = _CALL.match(_text(response))
    if match is None or match.group("name") not in tool_names:
        return response
    args = match.group("args").strip()
    if not args:
        parsed = {}
    elif match.group("open") == "(":
        parsed = _python_style_args(args)
    else:
        parsed = _gemma_style_args(args)
    if parsed is None:
        return response
    call = {"name": match.group("name"), "args": parsed, "id": f"recovered_{uuid.uuid4().hex[:12]}"}
    return AIMessage(content="", tool_calls=[call], response_metadata=response.response_metadata,
                     id=response.id)


def is_empty(response: AIMessage) -> bool:
    return not response.tool_calls and not _text(response).strip()


def fallback_line(messages: list[BaseMessage]) -> str:
    """What to say when the model said nothing twice: the short result of a tool that ran
    this turn (the work happened; only the sentence about it is missing), else ask again."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage):
            result = message.content if isinstance(message.content, str) else ""
            if result.strip() and len(result) <= MAX_SPOKEN_TOOL_RESULT:
                return result.strip()
            break
    return NOTHING_TO_SAY
