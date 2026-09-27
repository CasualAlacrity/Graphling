# Graphling

**ALICE** is a voice-first AI companion for Star Citizen — hold a hotkey, ask it something,
and it answers using live game-economy data, plans routes, and tracks your trade runs end to
end. Built as a real, demoable application for learning LangChain, LangGraph, LangSmith, and
Pydantic — not a toy chatbot.

## Features

- **Voice-first interaction** — push-to-talk speech-to-text (local Whisper) and spoken replies
  (ElevenLabs TTS). No mic or hotkey permissions yet? `PTT_MODE=text` swaps in a terminal prompt,
  same graph underneath.
- **19 tools on one agent** — live commodity/item/vehicle prices, refinery yields, mining
  locations, best-route and travel-time planning, and full trade-run tracking (buy/sell legs,
  milestones, profit).
- **Persistent trade-run tracker** — buy/sell legs, milestones, and profit, backed by a
  Postgres-backed ledger server. Survives restarts; it's a ledger, not a chat memory.
- **Server-mediated, multi-tenant backend** — a small FastAPI ledger server is the *only*
  thing that ever talks to Postgres. Pilots authenticate with Discord OAuth (the server holds
  the real client secret, never the desktop app); trade-run data and the shared UEX/wiki
  reference caches are pooled across every pilot's install, not siloed per machine.
- **On-topic guardrail as a graph node** — a dedicated classifier step declines off-topic
  requests before they ever reach the main agent, instead of relying on a prompt instruction.
- **Desktop overlay** (PySide6) — hotkey-toggled trade-run/filter panel that runs alongside the
  voice loop for at-a-glance state.
- **Swappable LLM backend** — OpenAI, Anthropic, or fully local via Ollama, one env var.
- **Full LangSmith tracing**, with the persona and classifier prompts themselves versioned in the
  LangSmith Hub rather than hardcoded strings.

## Architecture

Two processes: ALICE (voice + graph + overlay, one per pilot, never containerized) and a
small ledger server that's the *only* thing that ever opens a Postgres connection.

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
 │  API · trade-run actions · timers          │
 └──────────────────┬────────────────────────┘
                     │ HTTPS + JWT (from a one-time Discord login)
                     ▼
        ┌─────────────────────────────┐
        │   ledger server (FastAPI)   │  auth relay · trade-run CRUD ·
        │  the sole Postgres client   │  shared UEX/wiki reference cache
        └──────────────┬──────────────┘
                       ▼
                  PostgreSQL
        │
        ▼ (back in ALICE)
 text → speech (ElevenLabs)
        │
        ▼
   spoken / printed reply
```

`respond` and `tools` loop until the model stops requesting tool calls (standard LangGraph
`tools_condition` pattern). `classify_topic` and `respond` are separate graph nodes, not one
prompt doing both jobs — see [app/graph.py](app/graph.py). No tool ever holds a database
connection string: trade-run reads/writes and the shared UEX/wiki caches all go through the
ledger server over HTTPS, authenticated by a JWT the server issues after a one-time Discord
OAuth login (the server does the real code exchange with Discord's client secret; the
desktop client never holds one). See [docs/deploy.md](docs/deploy.md) for how the server's
deployed and [docs/todo.md](docs/todo.md)'s Phase 2 section for why this replaced direct
client-to-Postgres access.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| LLM orchestration | LangChain | Structured output, provider-agnostic model binding |
| Conversational graph | LangGraph | Explicit guardrail/tool-calling loop as a graph, not prompt logic |
| Observability | LangSmith | Full pipeline tracing; also hosts the persona/classifier prompts |
| Schemas | Pydantic | Structured LLM output and tool args |
| Trade-run storage | PostgreSQL + SQLAlchemy + Alembic | Durable, queryable ledger — not a semantic-retrieval problem |
| Backend API | FastAPI | Thin ledger + cache server; the only process that ever touches Postgres |
| Auth | Discord OAuth2 + JWT | Server-side code exchange (real client secret); relayed to the desktop client via a local loopback listener — no long-lived credentials on the client |
| Desktop overlay | PySide6 | LGPL licensing (vs. PyQt6) and real layout/styling power (vs. tkinter) |
| Voice | Whisper (local) + ElevenLabs | Free/local STT, natural-sounding TTS |
| Local infra | Docker Compose | Postgres for a locally-run ledger server (skip it and point at an already-deployed server instead) |

## Demo

_Coming soon — a 30–90s clip of a voice request, a tool call, and a trade-run update landing on
the overlay._

## Setup

**Requirements**

- Python 3.11
- A ledger server for ALICE to talk to (`app/server/`, see [Architecture](#architecture)) —
  either point `ALICE_API_URL` at one already running somewhere, or run your own locally:
  - Docker Desktop (Postgres for that local server)
  - A [Discord](https://discord.com/developers/applications) OAuth2 application, "Public
    Client" turned off, with a Client ID/Secret and a redirect URI matching your server —
    see [docs/deploy.md](docs/deploy.md)'s "Local dev" section
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
- A Discord account — the first thing ALICE does is open a browser for you to log in with it;
  there's no separate app-level password

**Install**

```bash
git clone <this repo>
cd Graphling
cp .env-template .env   # fill in your keys
```

Only running your own local ledger server (skip this if pointing `ALICE_API_URL` at one
someone else deployed): open `app/server/.env-template` and add its vars
(`TRADE_DB_URL`, `DISCORD_CLIENT_ID`/`SECRET`, `DISCORD_REDIRECT_URI`, `JWT_SECRET_KEY`)
into the same `.env` — skip `UEXCORP_API_KEY`/`UEXCORP_BEARER_TOKEN`, already there from
the client template above.

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

**Run the ledger server** (skip this and just set `ALICE_API_URL` if you're pointing at an
already-deployed one):
```bash
docker compose up -d postgres
cd app && ../.venv/bin/uvicorn server.main:app --reload
```

**Run**

One package, one command — voice and the overlay always run together:

| macOS | Windows |
|---|---|
| `./scripts/mac/run-alice.sh` | `scripts\windows\run-alice.bat` |

Hold `PTT_HOTKEY` (default `shift_r`) to talk, and `OVERLAY_HOTKEY` (default `F3`) to
toggle the trade-run overlay. macOS needs Microphone and Accessibility permissions
granted to whichever terminal app runs the script on first use — see the comments at the
top of `run-alice.sh` if a hotkey doesn't respond. No mic set up yet? Set
`PTT_MODE=text` in `.env` and type instead; everything downstream, including spoken
replies, runs identically. (`alice-voice` runs the voice loop without the overlay — quick
debugging only, not a supported run mode.)

**Tests**

```bash
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pytest
```

## Engineering highlights

- **Guardrails as a graph node, not a prompt instruction** — `classify_topic` runs a structured-
  output LLM call before `respond` ever sees the message, so an off-topic request is refused
  deterministically rather than hoping the system prompt holds.
- **Tool calling at real scale** — 19 tools bound to one model via `bind_tools`, spanning
  read-only lookups (prices, routes) and stateful writes (mark cargo acquired/sold, confirm
  loaded/unloaded) against a real Postgres-backed domain model.
- **Server-mediated multi-tenancy** — the desktop client used to hold a direct Postgres
  connection string; it now never touches the database at all. A FastAPI server (`app/server/`)
  is the sole owner of Postgres, reachable only over HTTPS and authenticated by a JWT issued
  after a real Discord OAuth code exchange (server-side, with a real client secret — not "the
  client says who it is"). The desktop app can't sit at the server's own OAuth redirect like a
  browser tab, so login relays through a local loopback listener on the pilot's machine instead.
  Deployed behind nginx on a shared box, `docker-compose`, migrations via Alembic.
- **Two deliberately separate persistence layers** — LangGraph's `MemorySaver` checkpointer
  handles short-term thread state; the trade-run tracker's Postgres tables (now owned
  exclusively by the ledger server) are the durable long-term store. They solve different
  timescales and are not conflated.
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
