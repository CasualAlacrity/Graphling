"""
Manual eval — the first seed case from docs/todo.md's Phase 3 dataset: the
best_route -> route_token -> start_trade_run cross-turn dependency.

NOT a pytest test. Deliberately outside tests/ (pyproject.toml's testpaths is
tests/ only) so a normal `pytest` run never touches it — this hits the real
UEX API, a real LLM, and a real Postgres database, and it actually creates a
TradeRun row. Needs a working .env (UEXCORP_*, an LLM provider key, TRADE_DB_URL
pointing at a running Postgres) and a real graph, not a fake/deterministic one —
that's the whole point right now: Phase 3's deterministic external layer doesn't
exist yet, and the thing being checked here is real model behavior, not code
logic a unit test could already cover.

What this checks: does the model faithfully carry route_token from best_route's
reply into the start_trade_run call on the very next turn, rather than
paraphrasing, dropping, or inventing one? That's the one genuinely new risk the
token-handoff design (docs/start-route-tool.md) introduced, and it's a property
of model behavior, not something code review can verify.

Usage:
    cd app && PYTHONPATH=. ../.venv/bin/python ../evals/start_trade_run_flow.py

Edit ORIGIN / SHIP below to match a ship + starting location your UEX account
actually has live route data for.
"""
import asyncio
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from graph import State, graph  # noqa: E402

ORIGIN = "Orison"
SHIP = "Railen"

TOKEN_RE = re.compile(r"route_token=([0-9a-f]{8})")


def _tool_calls(ai_message: AIMessage) -> list[dict]:
    return ai_message.tool_calls or []


def _tool_result_for(messages: list, tool_call_id: str) -> str | None:
    for m in messages:
        if isinstance(m, ToolMessage) and m.tool_call_id == tool_call_id:
            return m.content
    return None


async def _run_turn(label: str, text: str, thread_id: str) -> list:
    print(f"\n=== {label}: {text!r} ===")
    config = {"configurable": {"thread_id": thread_id}}
    result = await graph.ainvoke(State(messages=[HumanMessage(content=text)]), config=config)
    messages = result["messages"]

    # Walk backwards for the most recent AIMessage(s) with tool calls from this turn,
    # and report each one alongside its tool result.
    reported_any = False
    for m in messages:
        if isinstance(m, AIMessage) and _tool_calls(m):
            for call in _tool_calls(m):
                tool_result = _tool_result_for(messages, call["id"])
                print(f"  tool call: {call['name']}({call['args']})")
                print(f"  tool result: {tool_result!r}")
                reported_any = True
    if not reported_any:
        print("  (no tool calls this turn)")

    final = messages[-1]
    print(f"  final reply: {getattr(final, 'content', final)!r}")
    return messages


async def main() -> None:
    thread_id = str(uuid.uuid4())

    turn1 = await _run_turn("Turn 1", f"Give me the best route for my {SHIP}, starting near {ORIGIN}.", thread_id)

    token_from_turn1 = None
    for m in turn1:
        if isinstance(m, ToolMessage) and isinstance(m.content, str):
            match = TOKEN_RE.search(m.content)
            if match:
                token_from_turn1 = match.group(1)

    turn2 = await _run_turn("Turn 2", "Let's do it.", thread_id)

    token_used_in_turn2 = None
    for m in turn2:
        if isinstance(m, AIMessage):
            for call in _tool_calls(m):
                if call["name"] == "start_trade_run":
                    token_used_in_turn2 = call["args"].get("route_token")

    print("\n=== Check ===")
    print(f"  route_token from turn 1's best_route reply: {token_from_turn1}")
    print(f"  route_token start_trade_run was called with: {token_used_in_turn2}")
    if token_from_turn1 is None:
        print("  INCONCLUSIVE — best_route wasn't called, or didn't return a token. "
              "Read the turn 1 transcript above.")
    elif token_used_in_turn2 is None:
        print("  FAIL — start_trade_run was never called (or called without a route_token) "
              "on \"let's do it\". Read the turn 2 transcript above.")
    elif token_from_turn1 == token_used_in_turn2:
        print("  PASS — the model carried the exact token forward.")
    else:
        print("  FAIL — start_trade_run was called with a DIFFERENT token than the one "
              "best_route actually returned. The model paraphrased/invented one.")


if __name__ == "__main__":
    asyncio.run(main())
