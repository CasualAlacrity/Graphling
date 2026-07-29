"""
Push-to-talk voice loop for Uplink — runs standalone, alongside (not through)
the Chainlit app. Hold the PTT key, speak, release; Uplink replies out loud.

Usage: uplink-voice   (after `pip install -e .`)
On Windows: win-run-voice.bat
"""
import asyncio
import uuid

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from voice.audio_output import play_audio
from voice.tts import synthesize
from voice.voice_input import listen_once, load_whisper

load_dotenv()


async def run() -> None:
    # Imported here, not at module level — graph.py imports tools.timer_tool (a
    # submodule of this package), which would otherwise trigger this file's own
    # execution mid-way through graph.py's initialization, circling back on a
    # not-yet-finished module.
    from graph import State, graph, uex_client

    print("=" * 40)
    print("  UPLINK — Push to talk")
    print("=" * 40)

    load_whisper("base")
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Whisper has no built-in awareness that "Railen" or "Baijini Point" are expected
    # words — without a hint it renders an unusual proper noun as the nearest common
    # English word instead (e.g. "Railen" -> "railing"). Ship names are the highest-value
    # vocabulary to bias toward: they're short, numerous, and the exact case that broke.
    # Built once from the already-cached UEX catalog, not per-utterance.
    cache = await uex_client.get_uex_cache()
    ship_name_prompt = ", ".join(vehicle.name for vehicle in cache.vehicles)

    print("[Uplink] Ready. Hold PTT key to speak.")

    while True:
        text = listen_once(initial_prompt=ship_name_prompt)

        if not text:
            print("[Uplink] Nothing transcribed, listening again...")
            continue

        print(f"[Uplink] You: {text!r}")

        state_input = State(messages=[HumanMessage(content=text)])
        result = await graph.ainvoke(state_input, config=config)
        response_text = result["messages"][-1].content

        print(f"[Uplink] Uplink: {response_text!r}")

        audio = await synthesize(response_text)
        play_audio(audio)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[Uplink] Shutting down.")


if __name__ == "__main__":
    main()
