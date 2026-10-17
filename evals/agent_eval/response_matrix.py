"""What ALICE answers today: a CSV to take into Google Sheets and mark up with what she
*should* say (docs/todo.md priority 6: the response matrix the "fewer words per request"
work gets built against).

Not a scored eval like run.py, which runs against harness/world.py's small fixed world.
This runs the real graph against the real server (live UEX cache and wiki), and should be
run with LLM_PROVIDER=alice: model calls then go through the production gateway to
heket's pinned models with the prompts the server serves, so the answers are what a
pilot would hear today.

What it asks lives in matrix_cases.py: single-turn cases (every phrasing in cases.py, plus
SINGLE_TURNS) and multi-turn conversations (CONVERSATIONS, workflows like a whole trade
run by voice). Every turn is one CSV row; a conversation's turns share a "Conversation"
name and are numbered. The "Script" column flags the first turn of a conversation that
didn't call its intended tool ("off script") and every turn after it ("after divergence"):
those later lines answer a conversation the script didn't plan for, so judge them with that
in mind.

The pilot's trade runs and timers come from matrix_scenarios.py, never the real account:
each case starts from its scenario (no run, an active run, two runs, a running timer) on
a fake ledger that accepts writes, so "Bought 640 at 14.2" gets recorded and answered as
it would be live. The "Ledger writes" column lists what each turn wrote.

Turns are spaced out (--min-turn-seconds) to stay under the gateway's per-pilot limit of
30 model calls a minute (server settings.llm_requests_per_minute); a turn is 2-3 calls.

Usage (from the repo root, with a cached ALICE login):
    LLM_PROVIDER=alice .venv/bin/python evals/agent_eval/response_matrix.py
    LLM_PROVIDER=alice .venv/bin/python evals/agent_eval/response_matrix.py --only multi
    LLM_PROVIDER=alice .venv/bin/python evals/agent_eval/response_matrix.py --name "Full trade run"
    LLM_PROVIDER=alice .venv/bin/python evals/agent_eval/response_matrix.py --tools commodity_price_lookup
"""
import argparse
import asyncio
import csv
import json
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import matrix_scenarios  # noqa: E402
from cases import CASES  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,  # noqa: E402
    ToolMessage,
)
from matrix_cases import CONVERSATIONS, SINGLE_TURNS, on_script  # noqa: E402

from graph import graph  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"

CSV_FIELDS = [
    "#", "Type", "Conversation", "Turn", "Script", "Scenario", "Area", "Pilot says", "Intended tool",
    "Tool ALICE called", "Arguments", "ALICE says", "Words", "Seconds (no speech)", "Ledger writes", "Notes",
    "What ALICE should say", "Jeff's notes",
]

MIN_TURN_SECONDS = 9.0


@dataclass
class Case:
    """One thing to run: a single turn, or a conversation of several."""
    kind: str  # "single" or "multi-turn"
    name: str
    area: str
    scenario: str
    turns: list[tuple[str, str]]  # (pilot says, intended tool)


def _cases(only: str | None, name: str | None, tools: list[str] | None) -> list[Case]:
    cases = []
    for eval_case in CASES:
        for phrasing in eval_case.phrasings:
            tool = eval_case.expected_tool or "(none)"
            cases.append(Case("single", f"eval: {eval_case.id}", eval_case.expected_tool or "Social",
                              matrix_scenarios.NO_RUN, [(phrasing, tool)]))
    for single in SINGLE_TURNS:
        cases.append(Case("single", "", single.area, single.scenario, [(single.says, single.tool)]))
    for conversation in CONVERSATIONS:
        turns = []
        for turn in conversation.turns:
            turns.append((turn.says, turn.tool))
        cases.append(Case("multi-turn", conversation.name, conversation.area, conversation.scenario, turns))

    selected = []
    for case in cases:
        if only == "single" and case.kind != "single":
            continue
        if only == "multi" and case.kind != "multi-turn":
            continue
        if name and name.lower() not in case.name.lower():
            continue
        if tools and not _uses_any(case, tools):
            continue
        selected.append(case)
    return selected


def _uses_any(case: Case, tools: list[str]) -> bool:
    for _says, intended in case.turns:
        for tool in tools:
            if tool in intended:
                return True
    return False


async def _ask(utterance: str, thread_id: str) -> tuple[dict, float]:
    started = time.monotonic()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=utterance)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result, time.monotonic() - started


def _describe_turn(new_messages: list) -> tuple[str, str, str]:
    """(tools called, their arguments, ALICE's final words) for one turn's new messages."""
    tools_called = []
    arguments = []
    for message in new_messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                tools_called.append(call["name"])
                arguments.append(json.dumps(call["args"]))
    answer = ""
    for message in reversed(new_messages):
        if isinstance(message, AIMessage) and not message.tool_calls and message.content:
            answer = message.content if isinstance(message.content, str) else str(message.content)
            break
    for message in new_messages:
        if isinstance(message, ToolMessage) and not answer:
            answer = f"(no reply; last tool said: {str(message.content)[:200]})"
    return " + ".join(tools_called) or "(none)", " | ".join(arguments), answer


async def _run_case(case: Case, timeout_s: float, first_row_number: int) -> list[dict]:
    rows = []
    thread_id = str(uuid.uuid4())
    seen_messages = 0
    # Set at the first turn that leaves the script: every later turn answers a
    # conversation the script didn't plan for, so its row is marked as such.
    diverged = False
    with ExitStack() as stack:
        ledger = matrix_scenarios.install(case.scenario, stack)
        for turn_number, (says, intended) in enumerate(case.turns, start=1):
            row = {
                "#": first_row_number + len(rows), "Type": case.kind,
                "Conversation": case.name if case.kind == "multi-turn" else "",
                "Turn": turn_number if case.kind == "multi-turn" else "",
                "Scenario": case.scenario, "Area": case.area, "Pilot says": says, "Intended tool": intended,
            }
            rows.append(row)
            if diverged:
                row["Script"] = "after divergence"
            writes_before = len(ledger.writes)
            print(f"[{row['#']}] {case.name + ' / ' if case.kind == 'multi-turn' else ''}{says}", flush=True)
            try:
                result, seconds = await asyncio.wait_for(_ask(says, thread_id), timeout=timeout_s)
            except TimeoutError:
                row["Notes"] = f"timed out after {timeout_s:.0f}s; conversation stopped here"
                break
            except Exception as raised:
                row["Notes"] = f"error: {type(raised).__name__}: {raised}; conversation stopped here"
                await asyncio.sleep(MIN_TURN_SECONDS)
                break

            new_messages = result["messages"][seen_messages:]
            seen_messages = len(result["messages"])
            tools_called, arguments, answer = _describe_turn(new_messages)
            row["Tool ALICE called"] = tools_called
            row["Arguments"] = arguments
            row["ALICE says"] = answer
            row["Words"] = len(answer.split())
            row["Seconds (no speech)"] = round(seconds, 1)
            row["Ledger writes"] = "; ".join(ledger.writes[writes_before:])
            if case.kind == "multi-turn" and not diverged and on_script(intended, tools_called) is False:
                row["Script"] = "off script"
                diverged = True
            if result.get("social"):
                row["Notes"] = "classified social"
            elif result.get("on_topic") is False:
                row["Notes"] = "classified off-topic"
            print(f"    -> {tools_called}: {answer[:100]}", flush=True)
            await asyncio.sleep(max(0.0, MIN_TURN_SECONDS - seconds))
    return rows


async def main() -> int:
    global MIN_TURN_SECONDS
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", choices=("single", "multi"), default=None,
                        help="only single-turn cases, or only multi-turn conversations")
    parser.add_argument("--name", default=None, help="only conversations whose name contains this")
    parser.add_argument("--tools", default=None,
                        help="only cases with a turn whose intended tool is one of these, comma-separated")
    parser.add_argument("--timeout", type=float, default=240.0, help="seconds per turn before giving up")
    parser.add_argument("--min-turn-seconds", type=float, default=MIN_TURN_SECONDS,
                        help="space turns at least this far apart (gateway rate limit)")
    args = parser.parse_args()
    MIN_TURN_SECONDS = args.min_turn_seconds

    tools = args.tools.split(",") if args.tools else None
    cases = _cases(args.only, args.name, tools)
    turn_count = 0
    for case in cases:
        turn_count += len(case.turns)
    print(f"{len(cases)} cases, {turn_count} turns", flush=True)

    rows = []
    for case in cases:
        rows.extend(await _run_case(case, args.timeout, len(rows) + 1))

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"response-matrix__{datetime.now():%Y%m%d-%H%M%S}.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
