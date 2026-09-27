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

import asyncio
import csv
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

# judge.py builds its LLM at import time (same eager-construction style graph.py
# itself uses) -- load .env explicitly, here, before that import, rather than
# relying on graph.py's own load_dotenv() call happening to run first. Import
# order among local modules isn't something to depend on for this.
load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import CASES, HarnessCase  # noqa: E402
from judge import judge_response  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from graph import State, graph, prewarm  # noqa: E402
from harness.fakes import install_fakes  # noqa: E402
from turn_metrics import compute_turn_metrics  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# The JSON report is the full, structured source of truth compare.py reads. This is
# a flattened companion view of the same records -- one row per case x phrasing,
# scalar fields only (dict fields get JSON-stringified into their cell) -- for
# dragging into Excel/Sheets to eyeball trends across many runs over time, which a
# pairwise JSON diff isn't really the tool for.
CSV_FIELDS = [
    "model_provider", "model_name", "case_id", "phrasing",
    "expected_on_topic", "actual_on_topic", "on_topic_correct",
    "expected_tool", "actual_tool", "tool_correct",
    "expected_args", "actual_args", "args_correct",
    "response_correct", "overall_pass",
    "latency_ms", "tokens_in", "tokens_out", "tokens_total", "cost_usd",
    "final_response", "judge_reasoning",
]


def _write_csv(records: list[dict], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["expected_args"] = json.dumps(row["expected_args"])
            row["actual_args"] = json.dumps(row["actual_args"])
            writer.writerow(row)

MODEL_NAME_ENV_VAR = {
    "ollama": "OLLAMA_CHAT_MODEL",
    "openai": "OPENAI_CHAT_MODEL",
    "anthropic": "ANTHROPIC_CHAT_MODEL",
}


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


async def _run_one(case: HarnessCase, phrasing: str, model_provider: str, model_name: str) -> dict:
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    with install_fakes():
        start = time.monotonic()
        result = await graph.ainvoke(State(messages=[HumanMessage(content=phrasing)]), config=config)
        latency_ms = (time.monotonic() - start) * 1000

    messages = result["messages"]
    actual_on_topic = result["on_topic"]
    final_response = messages[-1].content

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
        "expected_args": case.expected_args,
        "actual_args": actual_args,
        "final_response": final_response,
        "judge_reasoning": None,
        "latency_ms": round(latency_ms, 1),
        "tokens_in": None,
        "tokens_out": None,
        "tokens_total": None,
        "cost_usd": None,
    }

    on_topic_correct = actual_on_topic == case.expected_on_topic
    record["on_topic_correct"] = on_topic_correct

    if not on_topic_correct:
        record["tool_correct"] = None
        record["args_correct"] = None
        record["response_correct"] = None
        record["overall_pass"] = False
        return record

    metrics = compute_turn_metrics(messages, latency_ms, model_provider, model_name)
    record["tokens_in"] = metrics.tokens_in
    record["tokens_out"] = metrics.tokens_out
    record["tokens_total"] = metrics.tokens_total
    record["cost_usd"] = metrics.cost_usd

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
        args_correct = tool_correct and actual_args is not None and all(
            str(actual_args.get(key, "")).lower() == str(value).lower()
            for key, value in (case.expected_args or {}).items()
        )
    else:
        tool_correct = actual_tool is None
        args_correct = True  # nothing to check

    record["tool_correct"] = tool_correct
    record["args_correct"] = args_correct

    verdict = await judge_response(phrasing, case.expected_outcome, tool_result, final_response)
    record["response_correct"] = verdict.response_correct
    record["judge_reasoning"] = verdict.reasoning

    record["overall_pass"] = tool_correct and args_correct and verdict.response_correct
    return record


async def main() -> int:
    model_provider = os.getenv("LLM_PROVIDER", "ollama")
    model_name = os.getenv(MODEL_NAME_ENV_VAR.get(model_provider, ""), "unknown")

    print(f"Running agent_eval against {model_provider}:{model_name}\n")
    await prewarm()

    records = []
    for case in CASES:
        for phrasing in case.phrasings:
            record = await _run_one(case, phrasing, model_provider, model_name)
            records.append(record)
            status = "PASS" if record["overall_pass"] else "FAIL"
            print(f"[{status}] {case.id!r} — {phrasing!r}")
            if not record["overall_pass"]:
                print(f"       on_topic: expected={record['expected_on_topic']} actual={record['actual_on_topic']}")
                print(f"       tool: expected={record['expected_tool']} actual={record['actual_tool']}")
                if record["judge_reasoning"]:
                    print(f"       judge: {record['judge_reasoning']}")

    total = len(records)
    passed = sum(1 for r in records if r["overall_pass"])
    total_cost = sum(r["cost_usd"] or 0 for r in records)
    total_tokens = sum(r["tokens_total"] or 0 for r in records)
    avg_latency = sum(r["latency_ms"] for r in records) / total if total else 0

    summary = {
        "model_provider": model_provider,
        "model_name": model_name,
        "timestamp": datetime.now(UTC).isoformat(),
        "total_cases": total,
        "passed": passed,
        "pass_rate": round(passed / total, 3) if total else 0,
        "total_cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
        "avg_latency_ms": round(avg_latency, 1),
    }

    print(f"\n=== Summary: {model_provider}:{model_name} ===")
    print(f"  {passed}/{total} passed ({summary['pass_rate']:.0%})")
    print(f"  total cost: ${summary['total_cost_usd']}")
    print(f"  total tokens: {summary['total_tokens']}")
    print(f"  avg latency: {summary['avg_latency_ms']} ms")

    RESULTS_DIR.mkdir(exist_ok=True)
    stem = f"{model_provider}-{model_name}__{datetime.now(UTC):%Y%m%d-%H%M%S}"
    json_path = RESULTS_DIR / f"{stem}.json"
    csv_path = RESULTS_DIR / f"{stem}.csv"
    json_path.write_text(json.dumps({"summary": summary, "records": records}, indent=2))
    _write_csv(records, csv_path)
    print(f"\nWrote {json_path}")
    print(f"Wrote {csv_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
