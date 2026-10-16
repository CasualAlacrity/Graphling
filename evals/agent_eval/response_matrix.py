"""What ALICE answers today, for every utterance we have: a CSV to take into Google Sheets
and mark up with what she *should* say (docs/todo.md priority 6: the response matrix that
the "fewer words per request" work gets built against).

Not a scored eval like run.py. That one runs against harness/world.py's fixed fake data,
so its answers only make sense for the few things the fixtures cover. This runs the real
graph against the real server (live UEX cache, wiki, the pilot's own ledger reads), and
should be run with LLM_PROVIDER=alice: model calls then go through the production gateway
to heket's pinned gemma4 build, with the prompts the server serves, so the
answers are what a pilot would hear today. A local Ollama gemma4 is a different build.

Utterances come from two places: every phrasing in cases.py, and the pilot phrasings
collected in docs/utterance-coverage.md (covered, behaviour, partial and gap lists).

**Ledger writes are blocked.** Starting a run, recording a purchase/sale, advancing a
leg etc. raise instead of reaching the server, so a matrix run can never touch real
trade runs. For those rows the tool and arguments ALICE chose are real; her spoken answer
is what she says after the write was refused, and the notes column says so.

Turns are spaced out (--min-turn-seconds) to stay under the gateway's per-pilot limit of
30 model calls a minute (server settings.llm_requests_per_minute); a turn is 2-3 calls.

Usage (from the repo root, with a cached ALICE login):
    LLM_PROVIDER=alice .venv/bin/python evals/agent_eval/response_matrix.py
    LLM_PROVIDER=alice .venv/bin/python evals/agent_eval/response_matrix.py --limit 5
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
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from cases import CASES  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

import ledger_client  # noqa: E402
from graph import graph  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"

LEDGER_WRITES = [
    "create_run_from_route", "advance_leg", "catch_up_before_transaction", "record_purchase",
    "record_sale", "update_transaction", "finalize_run", "delete_run",
]

ROUTE_SETUP = "Find me the best route from Orison in my Railen"
ORISON_ROUTE = "Give me a good route from Orison."

# Separates turns in the "Prior turn" column; a prior turn can be a tuple of several.
TURN_SEPARATOR = " >> "

# (area, utterance, intended tool, prior turn(s) in the same conversation or None).
# Intended tool is from docs/utterance-coverage.md; "(none)" means no tool is right,
# "(gap)" means nothing handles it yet.
COVERAGE_UTTERANCES = [
    ("Commodity prices", "Who's paying the most for quant right now?", "commodity_price_lookup", None),
    ("Commodity prices", "Is Laranite worth taking to Hurston?", "commodity_price_lookup", None),
    ("Commodity prices", "What about in Pyro?", "commodity_price_lookup", "Who's paying the most for quant right now?"),
    ("Commodity prices", "RMC at Grim Hex", "commodity_price_lookup", None),
    ("Items", "Where do I get a Pyro RYT multitool?", "item_price_lookup", None),
    ("Items", "Cheapest size 3 quantum drive", "item_price_lookup", None),
    ("Items", "Can I sell my old FS-9 anywhere?", "item_price_lookup", None),
    ("Vehicle purchase", "How much is a Hull A in-game?", "vehicle_purchase_lookup", None),
    ("Vehicle rental", "Can I rent a Cat for a day near Lorville?", "vehicle_rental_lookup", None),
    ("Mining", "Where's Quantainium on Daymar?", "mining_location_lookup", None),
    ("Mining", "Hadanite spots in Stanton", "mining_location_lookup", None),
    ("Refining", "Best refinery for Gold near me", "refinery_yield_lookup", None),
    ("Route finding", "I've got 96 SCU and 400k, what should I haul?", "best_route", None),
    ("Route finding", "Something short, I've got 20 minutes", "best_route", None),
    ("Route finding", "Nothing in Pyro", "best_route", ROUTE_SETUP),
    ("Route finding", "Find me a route from Oreson in my railing", "best_route", None),
    ("Route finding", ROUTE_SETUP, "best_route", None),
    ("Commit a route", "Yeah, do it", "start_trade_run", ROUTE_SETUP),
    ("Commit a route", "Start it but only 500 SCU", "start_trade_run", ROUTE_SETUP),
    ("Commit a route", "Let's do the second one", "start_trade_run", ROUTE_SETUP),
    ("Arrival", "Landed.", "mark_arrived", None),
    ("Arrival", "I'm at the buyer.", "mark_arrived", None),
    ("Buy", "Bought 640 at 14.2", "mark_cargo_acquired", None),
    ("Buy", "Got the copper", "mark_cargo_acquired", None),
    ("Load / unload", "Loaded, what's the ETA?", "confirm_cargo_loaded + estimate_travel_time", None),
    ("Load / unload", "Cargo's off", "confirm_cargo_unloaded", None),
    ("Sell", "Sold it all, 4,567 a unit", "mark_cargo_sold", None),
    ("Run status", "Where am I going again?", "trade_run_status", None),
    ("Run status", "What's after this?", "trade_run_status", None),
    ("Run status", "How much am I carrying?", "trade_run_status", None),
    ("Reassess a run", "Is it still worth going to Pyro?", "trade_advisor", None),
    ("Reassess a run", "Did I pick badly?", "trade_advisor", None),
    ("Container mix", "What crates do I buy?", "cargo_packing_suggestion", None),
    ("Container mix", "Can I fit 32s in the Railen?", "cargo_packing_suggestion", None),
    ("Travel time", "How far to Baijini?", "estimate_travel_time", None),
    ("Travel time", "Can I make it to Pyro?", "estimate_travel_time", None),
    ("Timers", "Loading takes 15, ping me", "start_timer", None),
    ("Timers", "Refinery job's 4 hours, remind me", "start_timer", None),
    ("Timers", "How long left?", "check_timer", None),
    ("Topic gate", "Morning ALICE, what's good to haul?", "best_route", None),
    ("Behaviour: decision-seeking", "Just pick one for me.", "(none)", ROUTE_SETUP),
    ("Behaviour: decision-seeking", "Which should I do?", "(none)", ROUTE_SETUP),
    ("Behaviour: unknowable", "Is Pyro safe right now?", "(none)", None),
    ("Partial: corrections", "No, it was 620, not 640.", "(gap) correct_trade_value", None),
    ("Partial: corrections", "Actually I sold at Area18.", "(gap) correct_trade_value", None),
    ("Partial: abandon / loss", "Scrap this run.", "(gap)", None),
    ("Partial: abandon / loss", "I died, cargo's gone.", "(gap)", None),
    ("Partial: setting the ship", "I'm in the C2 today.", "(gap)", None),
    ("Partial: departure", "Quantuming out.", "(gap)", None),
    ("Partial: partial sale", "Sold 300, keeping the rest for Area18.", "mark_cargo_sold", None),
    ("Gap: ledger history", "How much did I make today?", "(gap)", None),
    ("Gap: ledger history", "What was my last run?", "(gap)", None),
    ("Gap: ship facts", "How much SCU does a Hull C hold?", "(gap)", None),
    ("Gap: ship facts", "Can my Freelancer land at Orison?", "(gap)", None),
    ("Gap: meta-commands", "What can you do?", "(gap)", None),
    ("Gap: meta-commands", "Say that again.", "(gap)", ROUTE_SETUP),
    ("Gap: meta-commands", "Shorter.", "(gap)", ROUTE_SETUP),
    ("Gap: mining economics", "Is this rock worth it? 12% Quant, 8,000 mass.", "(gap)", None),
    # Jeff's reference conversation for follow-ups (docs/todo.md priority 6, search frame):
    # ALICE should ask which part "Pyro" changes, then answer a comparison with the delta.
    ("Follow-up: search frame", "What about Pyro?", "(clarify) best_route", ORISON_ROUTE),
    ("Follow-up: search frame", "No, just routes to Pyro.", "best_route (compare)",
     (ORISON_ROUTE, "What about Pyro?")),
]

CSV_FIELDS = [
    "#", "Source", "Area", "Prior turn", "Pilot says", "Intended tool", "Tool ALICE called",
    "Arguments", "ALICE says", "Words", "Seconds (no speech)", "Notes",
    "What ALICE should say", "Jeff's notes",
]


def _utterances(limit: int | None, tools: list[str] | None) -> list[dict]:
    rows = []
    for case in CASES:
        for phrasing in case.phrasings:
            rows.append({
                "Source": f"eval: {case.id}",
                "Area": case.expected_tool or "Topic gate",
                "Prior turn": "",
                "Pilot says": phrasing,
                "Intended tool": case.expected_tool or "(none)",
            })
    for area, utterance, tool, prior in COVERAGE_UTTERANCES:
        rows.append({
            "Source": "utterance-coverage.md",
            "Area": area,
            "Prior turn": _prior_text(prior),
            "Pilot says": utterance,
            "Intended tool": tool,
        })
    if tools:
        wanted = []
        for row in rows:
            for tool in tools:
                if tool in row["Intended tool"]:
                    wanted.append(row)
                    break
        rows = wanted
    if limit is not None:
        rows = rows[:limit]
    return rows


def _prior_turns(prior: str | tuple[str, ...] | None) -> list[str]:
    if prior is None:
        return []
    if isinstance(prior, str):
        return [prior]
    return list(prior)


def _prior_text(prior: str | tuple[str, ...] | None) -> str:
    return TURN_SEPARATOR.join(_prior_turns(prior))


def _blocked_write(name: str):
    async def refuse(*args, **kwargs):
        raise RuntimeError(f"response matrix dry run: {name} was not sent to the ledger")
    return refuse


def _last_turn(messages: list, utterance: str) -> list:
    """The messages from the final human turn onward, so a prior setup turn's tool calls
    don't get attributed to the utterance under test."""
    start = 0
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and message.content == utterance:
            start = index
    return messages[start:]


MIN_TURN_SECONDS = 9.0


async def _ask(utterance: str, thread_id: str) -> dict:
    started = time.monotonic()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=utterance)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    # Pacing, not part of the measured time: _run_row reads the clock before sleeping.
    leftover = MIN_TURN_SECONDS - (time.monotonic() - started)
    result["_pace_s"] = max(0.0, leftover)
    return result


async def _run_row(row: dict, timeout_s: float) -> None:
    thread_id = str(uuid.uuid4())
    notes = []
    started = time.monotonic()
    try:
        prior_turns = []
        if row["Prior turn"]:
            prior_turns = row["Prior turn"].split(TURN_SEPARATOR)
        for prior_turn in prior_turns:
            setup = await asyncio.wait_for(_ask(prior_turn, thread_id), timeout=timeout_s)
            await asyncio.sleep(setup["_pace_s"])
            started = time.monotonic()
        result = await asyncio.wait_for(_ask(row["Pilot says"], thread_id), timeout=timeout_s)
    except TimeoutError:
        row["Notes"] = f"timed out after {timeout_s:.0f}s"
        return
    except Exception as raised:
        row["Notes"] = f"error: {type(raised).__name__}: {raised}"
        await asyncio.sleep(MIN_TURN_SECONDS)
        return
    seconds = time.monotonic() - started
    await asyncio.sleep(result["_pace_s"])

    turn = _last_turn(result["messages"], row["Pilot says"])
    tools_called = []
    arguments = []
    for message in turn:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                tools_called.append(call["name"])
                arguments.append(json.dumps(call["args"]))
        if isinstance(message, ToolMessage) and "response matrix dry run" in str(message.content):
            notes.append("ledger write blocked: answer is after the refusal")

    answer = ""
    for message in reversed(turn):
        if isinstance(message, AIMessage) and not message.tool_calls and message.content:
            answer = message.content if isinstance(message.content, str) else str(message.content)
            break
    if result.get("on_topic") is False:
        notes.append("classified off-topic")

    row["Tool ALICE called"] = " + ".join(tools_called) or "(none)"
    row["Arguments"] = " | ".join(arguments)
    row["ALICE says"] = answer
    row["Words"] = len(answer.split())
    row["Seconds (no speech)"] = round(seconds, 1)
    row["Notes"] = "; ".join(dict.fromkeys(notes))


async def main() -> int:
    global MIN_TURN_SECONDS
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=None, help="only the first N utterances")
    parser.add_argument("--tools", default=None,
                        help="only rows whose intended tool is one of these, comma-separated")
    parser.add_argument("--timeout", type=float, default=240.0, help="seconds per turn before giving up")
    parser.add_argument("--min-turn-seconds", type=float, default=MIN_TURN_SECONDS,
                        help="space turns at least this far apart (gateway rate limit)")
    args = parser.parse_args()
    MIN_TURN_SECONDS = args.min_turn_seconds

    tools = args.tools.split(",") if args.tools else None
    rows = _utterances(args.limit, tools)
    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"response-matrix__{datetime.now():%Y%m%d-%H%M%S}.csv"

    with ExitStack() as stack:
        for name in LEDGER_WRITES:
            stack.enter_context(patch.object(ledger_client, name, _blocked_write(name)))
        for number, row in enumerate(rows, start=1):
            row["#"] = number
            print(f"[{number}/{len(rows)}] {row['Pilot says']}", flush=True)
            await _run_row(row, args.timeout)
            print(f"    -> {row.get('Tool ALICE called', '')}: {str(row.get('ALICE says', ''))[:100]}", flush=True)

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
