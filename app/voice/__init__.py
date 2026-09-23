"""
Push-to-talk voice loop for ALICE. Hold the PTT key, speak, release; ALICE replies out
loud. Normally started alongside the overlay by overlay_app.py (the "just ALICE"
package) — this standalone entry point is for quick voice-only debugging only.

Usage: alice-voice   (after `pip install -e .`) — debugging only.
The supported way to run is `alice` (scripts/mac/run-alice.sh,
scripts/windows/run-alice.bat), which starts this loop alongside the overlay.
"""
import asyncio
import uuid

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from auth.discord_identity import get_pilot_identity
from db.current_user import set_current_user_id
from db.user_service import get_or_create_user
from voice.audio_output import play_audio
from voice.tts import synthesize
from voice.voice_input import listen_once, load_whisper

load_dotenv()


async def run() -> None:
    # Imported here, not at module level — graph.py imports tools.timer_tool (a
    # submodule of this package), which would otherwise trigger this file's own
    # execution mid-way through graph.py's initialization, circling back on a
    # not-yet-finished module.
    from graph import State, graph, prewarm, uex_client

    print("=" * 40)
    print("  ALICE — Push to talk")
    print("=" * 40)

    load_whisper("base")

    # Identity, the UEX vocabulary cache, and warming the LLMs (graph.prewarm — see its
    # own docstring for why) are all independent of each other — run them concurrently
    # rather than one after another. On a first-time install this also means the
    # prewarm and the UEX fetch happen *while* the pilot is off in their browser doing
    # the one-time Discord login, not queued up after it.
    identity, cache, _ = await asyncio.gather(
        get_pilot_identity(),
        uex_client.get_uex_cache(),
        prewarm(),
    )
    print(f"[ALICE] Signed in as {identity.username}.")
    # Scopes every trade_run_store read/write to this pilot for the rest of the process
    # (db/current_user.py) — must happen before anything in the overlay or voice loop can
    # touch the ledger, which is why it's right here rather than deferred into the loop.
    # get_or_create_user maps the Discord id to (and creates, on a first-ever login) the
    # local users row TradeRun/TradeLeg.user_id actually FKs to.
    user = await get_or_create_user(identity.user_id)
    set_current_user_id(user.id)
    # thread_id carries the pilot's id so a shared checkpointer (Phase 2) can tell whose
    # thread is whose — MemorySaver doesn't need it today (one process per pilot already
    # isolates them), but every thread_id being wrong-shaped until then is exactly the
    # kind of thing that's cheap to get right now and a migration to fix later.
    thread_id = f"{identity.user_id}:{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    # Whisper has no built-in awareness that "Railen" or "Baijini Point" are expected
    # words — without a hint it renders an unusual proper noun as the nearest common
    # English word instead (e.g. "Railen" -> "railing"). Ship names are the highest-value
    # vocabulary to bias toward: they're short, numerous, and the exact case that broke.
    ship_name_prompt = ", ".join(vehicle.name for vehicle in cache.vehicles)

    print("[ALICE] Ready. Hold PTT key to speak.")

    while True:
        text = listen_once(initial_prompt=ship_name_prompt)

        if not text:
            print("[ALICE] Nothing transcribed, listening again...")
            continue

        print(f"[ALICE] You: {text!r}")

        state_input = State(messages=[HumanMessage(content=text)])
        result = await graph.ainvoke(state_input, config=config)
        response_text = result["messages"][-1].content

        print(f"[ALICE] ALICE: {response_text!r}")

        audio = await synthesize(response_text)
        play_audio(audio)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[ALICE] Shutting down.")


if __name__ == "__main__":
    main()
