"""Covers transcript_log.py and the voice loop's use of it: the turn's own messages are
picked out of the whole thread, tool calls are paired with their results, and a turn
writes one readable line to today's local file."""
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import transcript_log
import voice


def _thread():
    return [
        HumanMessage("old question"),
        AIMessage("old answer"),
        HumanMessage("Where's Laranite cheap?"),
        AIMessage("", tool_calls=[{"name": "commodity_price_lookup", "args": {"commodity": "Laranite"}, "id": "c1"}]),
        ToolMessage("x" * 2000, tool_call_id="c1"),
        AIMessage("Baijini Point, 3.15 aUEC per SCU."),
    ]


def test_this_turns_messages_starts_after_the_last_pilot_message():
    turn = transcript_log.this_turns_messages(_thread())
    assert len(turn) == 3
    assert turn[-1].content == "Baijini Point, 3.15 aUEC per SCU."


def test_tool_calls_are_paired_with_their_truncated_results():
    calls = transcript_log.tool_calls_with_results(transcript_log.this_turns_messages(_thread()))
    assert calls[0]["name"] == "commodity_price_lookup"
    assert calls[0]["args"] == {"commodity": "Laranite"}
    assert calls[0]["result"].endswith("[500 more chars]")


async def test_a_turn_writes_one_line_with_what_was_heard_and_done(tmp_path, monkeypatch):
    monkeypatch.setenv("ALICE_TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(voice, "log_turn_metrics", lambda *args: None)

    async def fake_synthesize(text):
        return b"RIFF-audio", 120.0

    monkeypatch.setattr(voice, "synthesize", fake_synthesize)

    class FakeGraph:
        async def ainvoke(self, state, config):
            return {"messages": _thread(), "on_topic": True, "reason": "price question"}

    heard = {"mode": "voice", "audio_s": 2.1, "rms": 0.05, "whisper_model": "small", "text": "Where's Laranite cheap?"}
    audio = await voice._run_turn(FakeGraph(), {"configurable": {}}, "jeff:1", 1, heard["text"], 900.0, heard, 0.0)

    assert audio == b"RIFF-audio"
    [log_file] = tmp_path.glob("*.jsonl")
    record = json.loads(log_file.read_text().strip())
    assert record["event"] == "turn"
    assert record["heard"]["text"] == "Where's Laranite cheap?"
    assert record["heard"]["whisper_model"] == "small"
    assert record["on_topic"] is True
    assert record["tools"][0]["name"] == "commodity_price_lookup"
    assert record["reply"] == "Baijini Point, 3.15 aUEC per SCU."
    assert record["spoken"] is True
    assert record["timings"]["whisper_ms"] == 900.0
