# Graphling

**Uplink** is a voice-first AI companion for Star Citizen — hold a hotkey, ask it something,
and it answers using live game-economy data, plans routes, and tracks your trade runs end to
end. Built as a real, demoable application for learning LangChain, LangGraph, LangSmith, and
Pydantic — not a toy chatbot.

## Features

- **Voice-first interaction** — push-to-talk speech-to-text (local Whisper) and spoken replies
  (ElevenLabs TTS). No mic or hotkey permissions yet? `PTT_MODE=text` swaps in a terminal prompt,
  same graph underneath.
- **18 tools on one agent** — live commodity/item/vehicle prices, refinery yields, mining
  locations, best-route and travel-time planning, and full trade-run tracking (buy/sell legs,
  milestones, profit).
- **Persistent trade-run tracker** — buy/sell legs, milestones, and profit, backed by Postgres.
  Survives restarts; it's a ledger, not a chat memory.
- **On-topic guardrail as a graph node** — a dedicated classifier step declines off-topic
  requests before they ever reach the main agent, instead of relying on a prompt instruction.
- **Desktop overlay** (PySide6) — hotkey-toggled trade-run/filter panel that runs alongside the
  voice loop for at-a-glance state.
- **Swappable LLM backend** — OpenAI, Anthropic, or fully local via Ollama, one env var.
- **Full LangSmith tracing**, with the persona and classifier prompts themselves versioned in the
  LangSmith Hub rather than hardcoded strings.

## Architecture

```
 User (voice PTT or PTT_MODE=text)
        │
        ▼
 speech → text (local Whisper)
        │
        ▼
 ┌─────────────────┐
 │  classify_topic  │  LLM call, structured output (on_topic: bool)
 └────────┬─────────┘
          │
   on-topic │ off-topic
          │       │
          ▼       ▼
 ┌─────────────┐ ┌─────────┐
 │   respond   │ │ decline │
 │ (LLM, tools │ └─────────┘
 │  bound)     │
 └──────┬──────┘
        │ ⇄ tool calls
        ▼
 ┌───────────────────────────────────────────┐
 │  tools: UEX Corp API · Star Citizen Wiki   │
 │  API · trade-run Postgres store · timers   │
 └───────────────────────────────────────────┘
        │
        ▼
 text → speech (ElevenLabs)
        │
        ▼
   spoken / printed reply
```

`respond` and `tools` loop until the model stops requesting tool calls (standard LangGraph
`tools_condition` pattern). `classify_topic` and `respond` are separate graph nodes, not one
prompt doing both jobs — see [app/graph.py](app/graph.py).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| LLM orchestration | LangChain | Structured output, provider-agnostic model binding |
| Conversational graph | LangGraph | Explicit guardrail/tool-calling loop as a graph, not prompt logic |
| Observability | LangSmith | Full pipeline tracing; also hosts the persona/classifier prompts |
| Schemas | Pydantic | Structured LLM output and tool args |
| Trade-run storage | PostgreSQL + SQLAlchemy + Alembic | Durable, queryable ledger — not a semantic-retrieval problem |
| Desktop overlay | PySide6 | LGPL licensing (vs. PyQt6) and real layout/styling power (vs. tkinter) |
| Voice | Whisper (local) + ElevenLabs | Free/local STT, natural-sounding TTS |
| Local infra | Docker Compose | One-command Postgres for the trade-run store |

## Demo

_Coming soon — a 30–90s clip of a voice request, a tool call, and a trade-run update landing on
the overlay._

## Setup

**Requirements**

- Python 3.11
- Docker Desktop (Postgres for the trade-run tracker)
- A [LangSmith](https://smith.langchain.com) account + API key — not just for tracing, the
  persona and classifier prompts are pulled from the Hub at startup (a committed local snapshot,
  `prompts/.prompt_cache.json`, covers you if the pull fails)
- One LLM provider: an OpenAI or Anthropic API key, or a local [Ollama](https://ollama.com)
  install
- A [UEX Corp](https://uexcorp.space) API key + bearer token — required at startup for every
  entry point (`graph.py` constructs the client eagerly), not just the price-lookup tools
- An [ElevenLabs](https://elevenlabs.io) key — required. Voice and the overlay always run
  together as one package now, and spoken replies aren't conditional on `PTT_MODE`
  (that only swaps the input side for typed text)

**Install**

```bash
git clone <this repo>
cd Graphling
cp .env-template .env   # fill in your keys
```

macOS:
```bash
./scripts/mac/setup.sh
```

Windows:
```bat
scripts\windows\setup.bat
```

Either script creates a Python 3.11 virtualenv, installs dependencies, and — if Docker is
running — starts Postgres and applies migrations.

**Run**

One package, one command — voice and the overlay always run together:

| macOS | Windows |
|---|---|
| `./scripts/mac/run-overlay.sh` | `scripts\windows\run-overlay.bat` |

Hold `PTT_HOTKEY` (default `shift_r`) to talk, and `OVERLAY_HOTKEY` (default `F3`) to
toggle the trade-run overlay. macOS needs Microphone and Accessibility permissions
granted to whichever terminal app runs the script on first use — see the comments at the
top of `run-overlay.sh` if a hotkey doesn't respond. No mic set up yet? Set
`PTT_MODE=text` in `.env` and type instead; everything downstream, including spoken
replies, runs identically. (`run-voice.sh` also exists — voice alone, no overlay — for
quick debugging only, not a supported run mode.)

**Tests**

```bash
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pytest
```

## Engineering highlights

- **Guardrails as a graph node, not a prompt instruction** — `classify_topic` runs a structured-
  output LLM call before `respond` ever sees the message, so an off-topic request is refused
  deterministically rather than hoping the system prompt holds.
- **Tool calling at real scale** — 18 tools bound to one model via `bind_tools`, spanning
  read-only lookups (prices, routes) and stateful writes (mark cargo acquired/sold, confirm
  loaded/unloaded) against a real Postgres-backed domain model.
- **Two deliberately separate persistence layers** — LangGraph's `MemorySaver` checkpointer
  handles short-term thread state; the trade-run tracker's Postgres tables are the durable
  long-term store. They solve different timescales and are not conflated.
- **Voice pipeline concurrency fix** — the PTT listener used to open a fresh `pynput.Listener`
  every press/release cycle, which crashed with `SIGTRAP` once the overlay's own global-hotkey
  listener was also running (two low-level macOS event taps in one process, one repeatedly torn
  down). Fixed by making the listener a long-lived singleton — the normal `pynput` usage pattern.
- **External API integration** — UEX Corp for live commodity/vehicle economy data, plus a
  reverse-engineered Star Citizen Wiki endpoint found by inspecting their own route-planner's
  network calls (not in their published docs).
- **Provider-agnostic LLM layer** — `get_chat_llm()` swaps OpenAI, Anthropic, or local Ollama
  models behind one interface; no call-site changes.
- **Prompts as versioned artifacts** — the persona and classifier prompts live in the LangSmith
  Hub, not hardcoded strings, with a committed local snapshot as a fallback.

## Current work

Being upfront about what's next, not just what's built:

- **Evals** — none yet. Planned before the tool surface grows further, so regressions in
  tool-selection or persona behavior get caught automatically instead of by hand-testing.
- **Routing** — today's guardrail is a single binary on/off-topic classifier. No multi-intent
  routing yet.
- **Memory** — a generic long-term memory system (structured Postgres + semantic Chroma
  retrieval) was built and verified working, then deliberately shelved (parked on the
  `pilot-preference-memory` branch): it optimized suggestions but never felt like the AI knowing
  the pilot specifically. The trade-run tracker is meant to be the foundation richer memory could
  build on later, not a replacement for the idea.
