"""
Push-to-talk voice loop for ALICE. Hold the PTT key, speak, release; ALICE replies out
loud. Normally started alongside the overlay by overlay_app.py (the "just ALICE"
package) — this standalone entry point is for quick voice-only debugging only.

Usage: alice-voice   (after `pip install -e .`) — debugging only.
The supported way to run is `alice` (scripts/mac/run-alice.sh,
scripts/windows/run-alice.bat), which starts this loop alongside the overlay.
"""
import asyncio
import os
import time
import traceback
import uuid

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

import startup_status
from auth.discord_identity import LoginFailed, get_pilot_session
from transcript_log import this_turns_messages, tool_calls_with_results, write_event
from turn_metrics import TurnMetricsCallback, log_turn_metrics
from voice import voice_input
from voice.audio_output import play_audio
from voice.tts import synthesize
from voice.voice_input import listen_once, load_whisper

load_dotenv()


async def run() -> None:
    # Imported here, not at module level — graph.py imports tools.timer_tool (a
    # submodule of this package), which would otherwise trigger this file's own
    # execution mid-way through graph.py's initialization, circling back on a
    # not-yet-finished module.
    from graph import graph, prewarm, uex_client
    from tools.uexcorp.matching import cargo_capable_vehicles
    from vocabulary.aliases import MISHEARINGS
    from vocabulary.transcript import correct_transcript
    from voice.ship_hint import build_ship_hint

    print("=" * 40)
    print("  ALICE — Push to talk")
    print("=" * 40)

    load_whisper()

    # The session, the UEX vocabulary cache, and warming the LLMs (graph.prewarm — see
    # its own docstring for why) are all independent of each other — run them
    # concurrently rather than one after another. On a first-time install this also
    # means the prewarm and the UEX fetch happen *while* the pilot is off in their
    # browser doing the one-time login, not queued up after it.
    try:
        session, cache, _ = await asyncio.gather(
            get_pilot_session(),
            uex_client.get_uex_cache(),
            prewarm(),
        )
    except LoginFailed:
        # Refused (not in the beta), cancelled, or otherwise no token: nothing ALICE can
        # do without one. Raised on rather than exiting here, so the launcher
        # (overlay_app.start_voice) can show the pilot why in its window.
        raise
    print(f"[ALICE] Signed in as {session.username}.")
    # Ledger ownership is resolved server-side per-request from this session's token now
    # (server/dependencies.get_current_user) — nothing to set up client-side for it.
    # thread_id carries the pilot's name so a shared checkpointer (Phase 2) can tell whose
    # thread is whose — MemorySaver doesn't need it today (one process per pilot already
    # isolates them), but every thread_id being wrong-shaped until then is exactly the
    # kind of thing that's cheap to get right now and a migration to fix later.
    thread_id = f"{session.username}:{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    # Whisper has no built-in awareness that "Railen" or "Baijini Point" are expected
    # words — without a hint it renders an unusual proper noun as the nearest common
    # English word instead (e.g. "Railen" -> "railing"). Ship names are the highest-value
    # vocabulary to bias toward: they're short, numerous, and the exact case that broke.
    # Fitted to Whisper's prompt limit; every name no longer fits (voice/ship_hint.py).
    cargo_ship_names = []
    for vehicle in cargo_capable_vehicles(cache):
        cargo_ship_names.append(vehicle.name)
    ship_name_prompt = build_ship_hint(
        cargo_ship_names, list(MISHEARINGS["ship"].values()), voice_input.prompt_token_count,
    )

    write_event(
        "session_start", thread_id=thread_id, pilot=session.username,
        whisper_model=os.getenv("WHISPER_MODEL", "small"), llm_provider=os.getenv("LLM_PROVIDER"),
        tts_provider=os.getenv("TTS_PROVIDER", "elevenlabs"),
    )
    turn = 0

    print("[ALICE] Ready. Hold PTT key to speak.")
    startup_status.report(startup_status.READY)

    while True:
        text, whisper_ms = listen_once(initial_prompt=ship_name_prompt)
        # PTT release is approximately now, minus however long whisper itself took --
        # listen_once() returns right after transcribing, and that gap is the only
        # unaccounted time between release and this point. round_trip_ms
        # (docs/voice-stage-timing.md) starts at release, not at whenever
        # listen_once() happens to return.
        round_trip_start = time.monotonic() - (whisper_ms / 1000)

        heard = {**voice_input.last_capture, "text": text, "whisper_ms": round(whisper_ms)}
        if not text:
            print("[ALICE] Nothing transcribed, listening again...")
            write_event("ignored", thread_id=thread_id, reason=heard.pop("ignored", "Whisper heard no words"), **heard)
            continue

        # Known, unambiguous mishearings fixed before the model reads it (vocabulary/
        # transcript.py). The transcript log keeps what Whisper actually heard as `text`
        # and the fixed version as `corrected`, so new mishearings stay findable.
        corrected = correct_transcript(text)
        if corrected != text:
            print(f"[Voice] Corrected: {text!r} -> {corrected!r}")
            heard["corrected"] = corrected
            text = corrected

        print(f"[ALICE] You: {text!r}")
        turn += 1
        try:
            audio = await _run_turn(graph, config, thread_id, turn, text, whisper_ms, heard, round_trip_start)
        except Exception as exc:
            # One broken turn used to end the voice loop for the rest of the session.
            # Now it's printed, logged with what was heard, and ALICE listens again.
            traceback.print_exc()
            print("[ALICE] That turn failed; listening again.")
            write_event("turn", thread_id=thread_id, turn=turn, heard=heard, error=f"{type(exc).__name__}: {exc}")
            continue
        play_audio(audio)


async def _run_turn(
    graph, config: dict, thread_id: str, turn: int, text: str, whisper_ms: float, heard: dict,
    round_trip_start: float,
) -> bytes:
    """One pilot utterance through the graph and TTS. Returns the audio to play, after
    writing the turn's metrics and transcript lines."""
    metrics_callback = TurnMetricsCallback()
    # A plain dict, not a full State(...) object -- a pydantic instance's
    # model_dump() materializes every field's default (on_topic, reason,
    # pending_confirmation), which would overwrite the checkpointed values from
    # the *previous* turn before this turn's nodes ever run. A bare dict only
    # updates the key actually given, leaving everything else to the checkpoint.
    state_input = {"messages": [HumanMessage(content=text)]}
    graph_start = time.monotonic()
    result = await graph.ainvoke(state_input, config={**config, "callbacks": [metrics_callback]})
    timings = metrics_callback.timings
    timings.whisper_ms = whisper_ms
    timings.graph_ms = (time.monotonic() - graph_start) * 1000
    response_text = result["messages"][-1].content

    print(f"[ALICE] ALICE: {response_text!r}")

    tts_start = time.monotonic()
    audio, tts_first_chunk_ms = await synthesize(response_text)
    timings.tts_first_chunk_ms = tts_first_chunk_ms
    timings.tts_total_ms = (time.monotonic() - tts_start) * 1000

    # round_trip_ms stops here, at play_audio starting -- it blocks until playback
    # finishes (voice/audio_output.py), which is not part of this metric.
    timings.round_trip_ms = (time.monotonic() - round_trip_start) * 1000
    log_turn_metrics(timings, metrics_callback.usage, thread_id)

    turn_messages = this_turns_messages(result["messages"])
    write_event(
        "turn", thread_id=thread_id, turn=turn, heard=heard,
        on_topic=result.get("on_topic"), classify_reason=result.get("reason"),
        tools=tool_calls_with_results(turn_messages), reply=response_text, spoken=bool(audio),
        timings=timings.model_dump(), usd=round(metrics_callback.usage.cost_usd, 6),
    )
    return audio


def main() -> None:
    try:
        asyncio.run(run())
    except LoginFailed as failed:
        print(f"\n[ALICE] {failed}")
    except KeyboardInterrupt:
        print("\n[ALICE] Shutting down.")


if __name__ == "__main__":
    main()
