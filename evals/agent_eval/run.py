"""The agent-behavior eval harness — sends every phrasing of every case in
cases.py through the real graph/LLM, with harness/fakes.py's deterministic fake
world installed underneath, and grades what happened.

NOT a pytest test, same reasoning as evals/start_trade_run_flow.py: this is about
real model behavior (does IT pick the right tool, does ITS response hold up), not
code logic a unit test could already cover. Unlike that script, external data here
IS faked — that's the whole point of harness/world.py existing, so results are
reproducible regardless of what UEX/the wiki actually say today.

One run = one model: reads LLM_PROVIDER/*_CHAT_MODEL from the environment, same as
the real app. To benchmark a different model, set different env vars and run this
again — the two runs' report files are what evals/agent_eval/compare.py diffs.

Usage:
    cd app && PYTHONPATH=. ../.venv/bin/python ../evals/agent_eval/run.py
"""

import os

# Many repeated calls per run -- no reason to burn LangSmith trace quota on eval
# noise. Set before any langchain import, same reasoning as start_trade_run_flow.py.
os.environ.setdefault("LANGSMITH_TRACING", "false")

import argparse
import asyncio
import csv
import itertools
import json
import re
import sys
import textwrap
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

# Plain ANSI -- no new dependency, and Terminal.app/Windows Terminal/PowerShell all
# handle raw escape codes fine. Skipped when stdout isn't a real terminal (piped to
# a file, or run_batch.py's subprocess output in a non-interactive context) so logs
# don't fill up with escape-code noise.
_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text

# judge.py builds its LLM at import time (same eager-construction style graph.py
# itself uses) -- load .env explicitly, here, before that import, rather than
# relying on graph.py's own load_dotenv() call happening to run first. Import
# order among local modules isn't something to depend on for this.
load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import CASES, HarnessCase  # noqa: E402
from judge import extract_claims, judge_response  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from graph import (  # noqa: E402
    CACHE_MODE,
    CLASSIFY_HISTORY_TURNS,
    HISTORY_FULL_TOOL_RESULT_TURNS,
    HISTORY_TURNS,
    graph,
    prewarm,
)
from graph import tools as graph_tools  # noqa: E402
from harness.fakes import install_fakes  # noqa: E402
from turn_metrics import TurnMetricsCallback  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# The JSON report is the full, structured source of truth compare.py reads. This is
# a flattened companion view of the same records -- one row per case x phrasing,
# scalar fields only (dict fields get JSON-stringified into their cell) -- for
# dragging into Excel/Sheets to eyeball trends across many runs over time, which a
# pairwise JSON diff isn't really the tool for.
CSV_FIELDS = [
    "model_provider", "model_name", "case_id", "phrasing", "sample",
    "timed_out", "expected_on_topic", "actual_on_topic", "on_topic_correct",
    "expected_tool", "actual_tool", "tool_correct",
    "expected_args", "actual_args", "args_correct",
    "location_correct", "price_correct", "unit_correct", "claims_supported", "response_correct", "overall_pass",
    "latency_ms", "tokens_in", "tokens_out", "tokens_total", "cost_usd",
    "tokens_cache_read", "tokens_cache_write", "cache_hit_rate", "unpriced_calls", "cost_by_node",
    "classify_ms", "decline_ms", "respond_ms", "tool_ms",
    "recommended_terminal", "stated_price", "unsupported_claims",
    "final_response", "judge_reasoning",
]

# Independently scored, so a failure says WHICH thing broke. None = not applicable to the
# case (a greeting has no price). overall_pass is all applicable dimensions at once.
DIMENSIONS = [
    ("on_topic_correct", "right topic gate"),
    ("tool_correct", "right tool"),
    ("args_correct", "right arguments"),
    ("location_correct", "right location"),
    ("price_correct", "right price"),
    ("unit_correct", "right unit"),
    ("claims_supported", "no unsupported claims"),
    ("response_correct", "free-text judge"),
]


def _write_csv(records: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["expected_args"] = json.dumps(row["expected_args"])
            row["unsupported_claims"] = json.dumps(row.get("unsupported_claims"))
            row["actual_args"] = json.dumps(row["actual_args"])
            row["respond_ms"] = "+".join(f"{ms:.0f}" for ms in row["respond_ms"])
            row["tool_ms"] = ", ".join(f"{name}={ms:.0f}" for name, ms in row["tool_ms"])
            row["cost_by_node"] = json.dumps(row["cost_by_node"])
            writer.writerow(row)

MODEL_NAME_ENV_VAR = {
    "ollama": "OLLAMA_CHAT_MODEL",
    "openai": "OPENAI_CHAT_MODEL",
    "anthropic": "ANTHROPIC_CHAT_MODEL",
}


def _resolve_model_name(model_provider: str) -> str:
    """Single model name for the whole run, except Ollama's optional per-role split
    (llm.py's get_chat_llm/get_classification_llm, OLLAMA_CHAT_MODEL vs
    OLLAMA_CLASSIFICATION_MODEL) -- when those two differ, report a composite label
    rather than silently naming the run after only one of the two models that
    actually ran. turn_metrics.DEFAULT_LOCAL_POWER_WATTS covers the cost lookup for
    a label like this with no matching table entry."""
    if model_provider == "alice":
        # The gateway picks the real model server-side; per-call models (and fallbacks)
        # are in each record's cost_by_node / turn usage, not knowable up front.
        return "gateway"
    if model_provider != "ollama":
        return os.getenv(MODEL_NAME_ENV_VAR.get(model_provider, ""), "unknown")

    respond_model = os.getenv("OLLAMA_CHAT_MODEL", "unknown")
    classify_model = os.getenv("OLLAMA_CLASSIFICATION_MODEL") or respond_model
    if respond_model == classify_model:
        return respond_model
    return f"respond={respond_model}+classify={classify_model}"


def _tool_calls(messages: list) -> list[dict]:
    calls = []
    for message in messages:
        if isinstance(message, AIMessage):
            calls.extend(message.tool_calls or [])
    return calls


def _tool_result_for(messages: list, tool_call_id: str) -> str | None:
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id == tool_call_id:
            return message.content
    return None


def _schema_defaults(tool_name: str) -> dict:
    for tool in graph_tools:
        if tool.name == tool_name:
            return {name: field.default for name, field in tool.args_schema.model_fields.items()}
    return {}


def _set_args(args: dict | None, defaults: dict) -> dict:
    """The args that actually say something: a tool call may or may not spell out an arg
    it left at its schema default (omitted vs `"require_autoload": false`), and those
    must compare equal."""
    result = {}
    for key, value in (args or {}).items():
        if value is None or value == "" or (key in defaults and value == defaults[key]):
            continue
        # Letters and digits only: the model may normalize a spoken name ("Grim Hex" ->
        # "GrimHEX"), and the tools fuzzy-match names anyway, so spacing/case aren't a miss.
        result[key] = "".join(ch for ch in str(value).lower() if ch.isalnum())
    return result


def _args_match(expected: dict | None, actual: dict | None, tool_name: str, exact: bool) -> bool:
    defaults = _schema_defaults(tool_name)
    wanted = _set_args(expected, defaults)
    got = _set_args(actual, defaults)
    # An expected value equal to the default means "must NOT be set" -- _set_args drops it,
    # so without this an expected {"sell_at_nqa_only": False} would pass vacuously.
    must_be_unset = [key for key, value in (expected or {}).items() if key in defaults and value == defaults[key]]
    if any(key in got for key in must_be_unset):
        return False
    if exact:
        return got == wanted
    return all(got.get(key) == value for key, value in wanted.items())


async def _run_one(
    case: HarnessCase, phrasing: str, model_provider: str, model_name: str, run_timeout_s: float,
) -> dict:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    metrics_callback = TurnMetricsCallback()
    with install_fakes():
        start = time.monotonic()
        timed_out = False
        try:
            # A model call that never returns would otherwise freeze the whole suite
            # (one did: 6 runs in 34 minutes). Bounded, it becomes one recorded failure.
            result = await asyncio.wait_for(
                graph.ainvoke(
                    # A plain dict, not State(...) -- see voice/__init__.py's note on why a
                    # full pydantic instance would clobber checkpointed fields with defaults.
                    # Harmless here (a fresh thread_id every call means nothing to clobber),
                    # but this is the pattern everything should follow regardless.
                    {"messages": [HumanMessage(content=phrasing)]},
                    config={**config, "callbacks": [metrics_callback]},
                ),
                timeout=run_timeout_s,
            )
        except TimeoutError:
            timed_out = True
            result = {"messages": [HumanMessage(content=phrasing)], "on_topic": None}
        latency_ms = (time.monotonic() - start) * 1000
    timings = metrics_callback.timings
    usage = metrics_callback.usage

    messages = result["messages"]
    actual_on_topic = result["on_topic"]
    final_response = "(timed out)" if timed_out else messages[-1].content

    calls = _tool_calls(messages)
    actual_tool = calls[0]["name"] if calls else None
    actual_args = calls[0]["args"] if calls else None
    tool_result = _tool_result_for(messages, calls[0]["id"]) if calls else None

    record = {
        "case_id": case.id,
        "phrasing": phrasing,
        "model_provider": model_provider,
        "model_name": model_name,
        "expected_on_topic": case.expected_on_topic,
        "actual_on_topic": actual_on_topic,
        "expected_tool": case.expected_tool,
        "actual_tool": actual_tool,
        "expected_args": case.expected_args_for(phrasing),
        "actual_args": actual_args,
        "final_response": final_response,
        "judge_reasoning": None,
        "timed_out": timed_out,
        # Every scored dimension exists on every record (None = not applicable), so
        # readers never have to guess whether a missing key means "skipped" or "bug".
        "tool_correct": None, "args_correct": None, "location_correct": None, "price_correct": None,
        "unit_correct": None, "claims_supported": None, "response_correct": None,
        "recommended_terminal": None, "stated_price": None, "unsupported_claims": None,
        "latency_ms": round(latency_ms, 1),
        # Every turn's usage, whatever the outcome -- a wrongly-declined or misrouted turn
        # still billed. tokens_in is the whole prompt (cached parts included), matching
        # what older reports meant by it; the cache columns say how much of it was cached.
        # The judge's own calls aren't in here: it runs outside graph.ainvoke, so outside
        # metrics_callback -- eval overhead, not what a pilot's turn costs.
        "tokens_in": usage.prompt_tokens,
        "tokens_out": usage.output_tokens,
        "tokens_total": usage.prompt_tokens + usage.output_tokens,
        "tokens_cache_read": usage.cache_read_tokens,
        "tokens_cache_write": usage.cache_write_tokens,
        "cache_hit_rate": round(usage.cache_hit_rate, 3) if usage.cache_hit_rate is not None else None,
        "cost_usd": round(usage.cost_usd, 6),
        "cost_by_node": {node: round(cost, 6) for node, cost in usage.cost_by_node.items()},
        "unpriced_calls": usage.unpriced_calls,
        "classify_ms": round(timings.classify_ms, 1) if timings.classify_ms is not None else None,
        "decline_ms": round(timings.decline_ms, 1) if timings.decline_ms is not None else None,
        "respond_ms": [round(ms, 1) for ms in timings.respond_ms],
        "tool_ms": [(name, round(ms, 1)) for name, ms in timings.tool_ms],
    }

    if timed_out:
        # Nothing to score -- every dimension stays None, and the row shows PASS = fail.
        record["on_topic_correct"] = None
        record["overall_pass"] = False
        return record

    on_topic_correct = actual_on_topic == case.expected_on_topic
    record["on_topic_correct"] = on_topic_correct

    if not on_topic_correct:
        record["tool_correct"] = None
        record["args_correct"] = None
        record["response_correct"] = None
        record["overall_pass"] = False
        return record

    if not case.expected_on_topic:
        # Declined correctly -- decline_topic's reply is deterministic, canned text
        # (graph.py), nothing for the judge to grade. Still worth asserting no tool
        # was spuriously called on the decline path.
        record["tool_correct"] = actual_tool is None
        record["args_correct"] = None
        record["response_correct"] = None
        record["overall_pass"] = record["tool_correct"]
        return record

    if case.expected_tool is not None:
        tool_correct = actual_tool == case.expected_tool
        args_correct = tool_correct and actual_args is not None and _args_match(
            case.expected_args_for(phrasing), actual_args, case.expected_tool, case.exact_args,
        )
    else:
        tool_correct = actual_tool is None
        args_correct = True  # nothing to check

    record["tool_correct"] = tool_correct
    record["args_correct"] = args_correct

    if case.args_only:
        record["response_correct"] = None
        record["overall_pass"] = tool_correct and args_correct
        return record

    if not case.structured_reply:
        verdict = await judge_response(phrasing, case.expected_outcome, tool_result, final_response)
        record["response_correct"] = verdict.response_correct
        record["judge_reasoning"] = verdict.reasoning
        record["overall_pass"] = tool_correct and args_correct and verdict.response_correct
        return record

    if not tool_correct:
        # The reply was built from the wrong tool's result (or none), so location/price/
        # unit/claims would all fail as a cascade of the tool miss and bury it. Left None
        # = not evaluated; tool_correct already carries the failure.
        record["overall_pass"] = False
        return record

    claims = await extract_claims(phrasing, tool_result, final_response)
    record["recommended_terminal"] = claims.recommended_terminal
    record["stated_price"] = claims.price_for_recommended_terminal
    record["unsupported_claims"] = claims.unsupported_claims

    record["location_correct"] = (
        None if case.expected_location is None else _same_name(case.expected_location, claims.recommended_terminal)
    )
    record["price_correct"] = (
        None if case.expected_price is None
        else _price_matches(case.expected_price, claims.price_for_recommended_terminal)
    )
    record["unit_correct"] = None if case.expected_unit is None else _says_unit(case.expected_unit, final_response)
    record["claims_supported"] = not claims.unsupported_claims

    checked = [record[key] for key, _ in DIMENSIONS if record.get(key) is not None]
    record["overall_pass"] = all(checked)
    return record


def _same_name(expected: str, actual: str | None) -> bool:
    """Either name containing the other, letters/digits only -- the reply may say
    "Baijini Point in Pyro" for "Baijini Point"."""
    if not actual:
        return False
    want = "".join(ch for ch in expected.lower() if ch.isalnum())
    got = "".join(ch for ch in actual.lower() if ch.isalnum())
    return want in got or got in want


def _price_matches(expected: float, actual: float | None, tolerance: float = 0.01) -> bool:
    return actual is not None and abs(actual - expected) <= expected * tolerance


def _says_unit(unit: str, response: str) -> bool:
    return re.search(rf"\b{re.escape(unit)}\b", response, re.IGNORECASE) is not None


TABLE_COLUMNS = [
    ("on_topic_correct", "topic"),
    ("tool_correct", "tool"),
    ("args_correct", "args"),
    ("location_correct", "location"),
    ("price_correct", "price"),
    ("unit_correct", "unit"),
    ("claims_supported", "grounded"),
    ("response_correct", "judge"),
    ("overall_pass", "PASS"),
]
PHRASING_WIDTH = 46
CELL_WIDTH = 9


def _cell(values: list) -> str:
    """One table cell from every sample of a phrasing: all passed, all failed, not
    applicable, or (samples disagree) k/N in yellow -- the flaky ones."""
    scored = [value for value in values if value is not None]
    if not scored:
        return _c("-".center(CELL_WIDTH), "2")
    if all(scored):
        return _c("\u2713".center(CELL_WIDTH), "32")
    if not any(scored):
        return _c("\u2717".center(CELL_WIDTH), "1;31")
    return _c(f"{sum(scored)}/{len(scored)}".center(CELL_WIDTH), "33")


def render_table(records: list[dict]) -> str:
    """One row per phrasing, grouped under its case, a column per scored dimension:
    \u2713 passed, \u2717 failed, - not applicable (or not evaluated after a tool miss),
    k/N when a phrasing was sampled more than once and the samples disagree. The bottom
    row is passed/total per column over every sample. "grounded" is the unsupported-claims
    check (\u2713 = nothing the reply said went beyond the tool result). Columns nothing
    scored (the free-text judge, today) are dropped."""
    columns = [
        (key, label) for key, label in TABLE_COLUMNS
        if any(record.get(key) is not None for record in records)
    ]
    header = " " * (PHRASING_WIDTH + 2) + "".join(label.center(CELL_WIDTH) for _, label in columns)
    lines = [_c(header, "1")]

    current_case = None
    for (case_id, phrasing), group in itertools.groupby(records, key=lambda r: (r["case_id"], r["phrasing"])):
        group = list(group)
        if case_id != current_case:
            current_case = case_id
            lines.append(_c(case_id, "1;36"))
        shown = phrasing if len(phrasing) <= PHRASING_WIDTH else phrasing[: PHRASING_WIDTH - 1] + "\u2026"
        row = "  " + shown.ljust(PHRASING_WIDTH)
        lines.append(row + "".join(_cell([record.get(key) for record in group]) for key, _ in columns))

    totals = "  " + _c("passed / total".ljust(PHRASING_WIDTH), "1")
    for key, _ in columns:
        applicable = [record[key] for record in records if record.get(key) is not None]
        text = f"{sum(applicable)}/{len(applicable)}"
        color = "32" if sum(applicable) == len(applicable) else "33"
        totals += _c(text.center(CELL_WIDTH), color)
    lines.append("-" * (PHRASING_WIDTH + 2 + CELL_WIDTH * len(columns)))
    lines.append(totals)
    return "\n".join(lines)


async def main() -> int:
    model_provider = os.getenv("LLM_PROVIDER", "ollama")
    model_name = _resolve_model_name(model_provider)

    print(_c(f"Running agent_eval against {model_provider}:{model_name}\n", "1;36"))
    await prewarm()

    # `run.py [--samples N] [case_id ...]` -- case ids to run just those cases; --samples
    # to run every phrasing N times. One sample per phrasing can't tell a flaky model from
    # a broken tool (the same phrasing passes one run and fails the next).
    parser = argparse.ArgumentParser()
    parser.add_argument("case_ids", nargs="*")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds before one run is abandoned")
    args = parser.parse_args()
    samples = max(1, args.samples)
    wanted_ids = set(args.case_ids)
    cases = [case for case in CASES if not wanted_ids or case.id in wanted_ids]
    unknown_ids = wanted_ids - {case.id for case in CASES}
    if unknown_ids:
        print(_c(f"Unknown case id(s): {', '.join(sorted(unknown_ids))}", "1;31"))
        return 2

    records = []
    for case in cases:
        for phrasing, sample in itertools.product(case.phrasings, range(1, samples + 1)):
            record = await _run_one(case, phrasing, model_provider, model_name, args.timeout)
            record["sample"] = sample
            records.append(record)
            if record["overall_pass"]:
                status = _c("PASS", "32")
            else:
                status = _c("FAIL", "1;31")
            sample_note = f" (sample {sample}/{samples})" if samples > 1 else ""
            print(f"[{status}] {case.id!r} — {phrasing!r}{sample_note}")
            if not record["overall_pass"]:
                # Only print the field(s) that actually caused the fail -- a mismatch
                # is often just one of these, and printing all three every time buried
                # the one that mattered under two that already matched.
                if record["timed_out"]:
                    print(f"       {_c('timeout', '1;31')}: no result within {args.timeout:.0f}s")
                elif not record["on_topic_correct"]:
                    label = _c("on_topic", "1;31")
                    print(f"       {label}: expected={record['expected_on_topic']} actual={record['actual_on_topic']}")
                if record["tool_correct"] is False:
                    label = _c("tool", "1;31")
                    print(f"       {label}: expected={record['expected_tool']} actual={record['actual_tool']}")
                if record["args_correct"] is False:
                    label = _c("args", "1;31")
                    print(f"       {label}: expected={record['expected_args']} actual={record['actual_args']}")
                for key in ("location_correct", "price_correct", "unit_correct"):
                    if record.get(key) is False:
                        label = _c(key.removesuffix("_correct"), "1;31")
                        print(
                            f"       {label}: expected location/price/unit="
                            f"{case.expected_location}/{case.expected_price}/{case.expected_unit}, reply gave "
                            f"{record.get('recommended_terminal')!r} at {record.get('stated_price')!r}"
                        )
                if record.get("claims_supported") is False:
                    label = _c("claims", "1;31")
                    print(f"       {label}: unsupported: {record['unsupported_claims']}")
                if record.get("response_correct") is False and record["judge_reasoning"]:
                    wrapped = textwrap.fill(
                        record["judge_reasoning"],
                        width=100,
                        initial_indent="       judge: ",
                        subsequent_indent="              ",
                    )
                    print(_c(wrapped, "33"))

    total = len(records)
    passed = sum(1 for r in records if r["overall_pass"])
    total_cost = sum(r["cost_usd"] or 0 for r in records)
    total_tokens = sum(r["tokens_total"] or 0 for r in records)
    total_prompt = sum(r["tokens_in"] or 0 for r in records)
    total_cache_read = sum(r["tokens_cache_read"] or 0 for r in records)
    total_unpriced = sum(r["unpriced_calls"] or 0 for r in records)
    avg_latency = sum(r["latency_ms"] for r in records) / total if total else 0

    dimension_scores = {}
    for key, _label in DIMENSIONS:
        applicable = [r[key] for r in records if r.get(key) is not None]
        if applicable:
            dimension_scores[key] = {"passed": sum(applicable), "total": len(applicable)}

    summary = {
        "dimensions": dimension_scores,
        "model_provider": model_provider,
        "model_name": model_name,
        "timestamp": datetime.now(UTC).isoformat(),
        "total_cases": total,
        "passed": passed,
        "pass_rate": round(passed / total, 3) if total else 0,
        "total_cost_usd": round(total_cost, 6),
        "cost_per_turn_usd": round(total_cost / total, 6) if total else 0,
        "total_tokens": total_tokens,
        "cache_hit_rate": round(total_cache_read / total_prompt, 3) if total_prompt else None,
        "unpriced_calls": total_unpriced,
        # Everything that changes what a turn costs, so two reports' costs can be told
        # apart by config rather than guessed at. Judge too: it grades, it isn't billed here.
        "config": {
            "judge_provider": os.getenv("HARNESS_JUDGE_PROVIDER", "openai"),
            "judge_model": os.getenv("HARNESS_JUDGE_MODEL", "gpt-4o-mini"),
            "history_turns": HISTORY_TURNS,
            "history_full_tool_result_turns": HISTORY_FULL_TOOL_RESULT_TURNS,
            "classify_history_turns": CLASSIFY_HISTORY_TURNS,
            "anthropic_cache_mode": CACHE_MODE,
            "anthropic_cache_ttl": os.getenv("ANTHROPIC_CACHE_TTL", "5m"),
        },
        "avg_latency_ms": round(avg_latency, 1),
    }

    print(_c(f"\n=== Summary: {model_provider}:{model_name} ===", "1;36"))
    print()
    print(render_table(records))
    print()
    pass_rate_color = "32" if passed == total else ("1;31" if passed == 0 else "33")
    pass_line = f"{passed}/{total} passed ({summary['pass_rate']:.0%})"
    print(f"  {_c(pass_line, pass_rate_color)}")
    print(f"  total cost: ${summary['total_cost_usd']}  (${summary['cost_per_turn_usd']}/turn)")
    if summary["cache_hit_rate"] is not None:
        print(f"  cache hit rate: {summary['cache_hit_rate']:.0%}")
    if total_unpriced:
        print(_c(f"  {total_unpriced} LLM call(s) had no MODEL_PRICING entry -- total cost is an undercount", "1;31"))
    print(f"  total tokens: {summary['total_tokens']}")
    print(f"  avg latency: {summary['avg_latency_ms']} ms")

    RESULTS_DIR.mkdir(exist_ok=True)
    # Ollama tags carry a colon (e.g. "qwen2.5:14b") -- fine in the JSON/CSV content
    # itself, but sanitized for the filename to avoid any tooling friction.
    safe_model_name = model_name.replace(":", "-").replace("/", "-")
    stem = f"{model_provider}-{safe_model_name}__{datetime.now(UTC):%Y%m%d-%H%M%S}"
    json_path = RESULTS_DIR / f"{stem}.json"
    csv_path = RESULTS_DIR / f"{stem}.csv"
    json_path.write_text(json.dumps({"summary": summary, "records": records}, indent=2))
    _write_csv(records, csv_path)
    print(f"\nWrote {json_path}")
    print(f"Wrote {csv_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
