"""A local, per-day log of what the pilot said and what ALICE did with it -- for
reconstructing a session afterwards: which phrasing worked, what Whisper actually heard,
which tool ran with which arguments, and where a turn went wrong.

One JSON object per line in <app data>/ALICE/transcripts/YYYY-MM-DD.jsonl (local date),
%APPDATA%\\ALICE\\transcripts on Windows. Events:

- session_start: who, which models, when.
- turn: one pilot utterance end to end -- what was heard (or typed), on-topic or not,
  every tool call with its arguments and (truncated) result, ALICE's reply, whether it
  was spoken, stage timings, and the error if the turn failed.
- ignored: a capture thrown away before Whisper (too short / too quiet) -- the "she
  didn't hear me" cases.

Local only. Unlike turn_metrics.jsonl (usage metadata, shaped for later upload), this
holds what the pilot said, so it never leaves the machine. Best effort: a failed write
is printed and the turn carries on.
"""
import json
import os
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from turn_metrics import alice_data_dir

# Tool results can be whole price tables; enough to see what ALICE was looking at.
MAX_TOOL_RESULT_CHARS = 1500


def transcript_dir() -> Path:
    return Path(os.getenv("ALICE_TRANSCRIPT_DIR", str(alice_data_dir() / "transcripts")))


def write_event(event: str, **fields) -> None:
    now = datetime.now().astimezone()
    record = {"ts": now.isoformat(timespec="milliseconds"), "event": event, **fields}
    path = transcript_dir() / f"{now.date().isoformat()}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        print(f"[ALICE] couldn't write transcript log ({exc}) -- continuing without it")


def this_turns_messages(messages: list) -> list:
    """The graph returns the whole thread; this turn is everything after the last
    HumanMessage."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index + 1:]
    return messages


def tool_calls_with_results(messages: list) -> list[dict]:
    results_by_id = {}
    for message in messages:
        if isinstance(message, ToolMessage):
            results_by_id[message.tool_call_id] = message.content

    calls = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            result = results_by_id.get(call.get("id"))
            if isinstance(result, str) and len(result) > MAX_TOOL_RESULT_CHARS:
                result = result[:MAX_TOOL_RESULT_CHARS] + f"... [{len(result) - MAX_TOOL_RESULT_CHARS} more chars]"
            calls.append({"name": call.get("name"), "args": call.get("args"), "result": result})
    return calls
