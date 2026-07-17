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

from graph import State, graph
from voice.audio_output import play_audio
from voice.tts import synthesize
from voice.voice_input import listen_once, load_whisper

load_dotenv()


async def run() -> None:
    print("=" * 40)
    print("  UPLINK — Push to talk")
    print("=" * 40)

    load_whisper("base")
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print("[Uplink] Ready. Hold PTT key to speak.")

    while True:
        text = listen_once()

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
