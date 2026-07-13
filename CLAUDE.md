# Graphling

## Purpose

A portfolio piece and a learning project for LangChain, LangGraph, LangSmith, and Pydantic. The
project is an AI companion — a persistent chat agent with a real memory system, not a toy chatbot.

v1 scope is intentionally narrow: one fixed persona, no multi-tenancy. The priority is a complete,
demoable application over breadth of features. Depth (a second persona, richer memory behavior)
is a "later, maybe" — don't build for it preemptively.

**Current active feature: the trade route tracker** (see `docs/trade-route-tracker.md`) — a
structured ledger of trade runs (buy/sell legs, milestones, profit), not the generic memory system
described below. That generic system was built, verified working, then deliberately shelved
(parked on the `pilot-preference-memory` branch) for lacking a concrete reason to need memory —
it optimized suggestions but never felt like the AI knowing the pilot specifically. Route tracking
is meant to be the foundation richer memory could build on later, not a replacement for the idea.

## Stack

- **LangChain** — LLM orchestration, structured output
- **LangGraph** — the conversational graph. Currently a simple `respond ⇄ tools` loop — the
  extraction/recall nodes mentioned in older notes were part of the now-shelved generic memory
  system, not built. Planned trade-route AI integration is ordinary tools (the existing
  `UplinkTool` pattern), not new graph nodes — see `docs/trade-route-tracker.md`.
- **LangSmith** — tracing/observability across the pipeline
- **Pydantic** — schemas for structured LLM output and tool args
- **Chainlit** — chat interface. Chosen over Streamlit/Gradio because it's built for LLM chat
  apps specifically and handles async streaming and LangChain callbacks natively.
- **PySide6** — overlay UI for the trade route tracker (manual entry first, AI-assisted later).
  Chosen over PyQt6 for licensing (LGPL vs. GPL/commercial) and over tkinter for layout/styling
  power. See `docs/trade-route-tracker.md` for the full reasoning, including why click-through —
  which would matter for a persistent HUD — turned out not to apply, since it's a toggle-open/
  close UI (hotkey-driven, like Arkanis's F3).
- **PostgreSQL + ChromaDB** — shelved along with the generic memory system above. Was a dual-write
  memory store (structured queries + semantic retrieval); see `lyra-memory-system-architecture.md`
  (in the user's Documents, not this repo) for the full design it was adapted from. The trade route
  tracker reuses the Postgres/SQLAlchemy/Alembic infrastructure (parked on
  `pilot-preference-memory`) but not ChromaDB — route data is structured/relational, not something
  needing semantic retrieval.
- **Ollama** — local model support alongside hosted providers (OpenAI/Anthropic), swappable via a
  provider-agnostic `get_llm()`-style helper.

## Key decisions

- **Structured output: LangChain's `with_structured_output()`, not Instructor.** The original
  memory design used Instructor + Pydantic for validated extraction output. Since the point of
  this project is demonstrating LangChain specifically, use LangChain's native structured-output
  binding on the same Pydantic schemas instead. Revisit Instructor only if LangChain's structured
  output hits a real limitation it can't solve.
- **No `RelationshipContext`/`Channel` scoping for v1.** The original design scoped memory to a
  relationship context to support multiple concurrent companion instances. Skipped for now —
  single implicit user/persona. If multi-persona support gets added later, this is an additive
  migration (add a context FK, backfill a default), not a redesign — no need for a placeholder
  column now.
- **LangGraph's checkpointer handles short-term/thread state; long-term persistence is a separate
  concern from it regardless of what backs it.** These solve different timescales — the
  checkpointer was never meant to substitute for durable storage. The generic memory system that
  originally backed long-term persistence is shelved (see Purpose); the trade route tracker's
  Postgres tables are the active long-term persistence for now.
- **Background jobs (session consolidation, decay, scheduled surfacing) live outside the graph.**
  They're cron-like jobs (e.g. APScheduler) that call into a small chain or the memory store when
  they fire — not per-message graph nodes.

## Environment

- `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` — enables tracing automatically,
  no code-side setup needed once loaded into the environment.
- `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` — required for this account, which is
  on LangSmith's EU tenant. Without it, the SDK defaults to the US endpoint and every trace
  ingestion call fails with a 403 Forbidden, even with a valid API key.
- Provider keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) as needed — omit entirely if running fully
  local via Ollama.
- `.env` is not auto-loaded; call `load_dotenv()` early in the entry point, or note if PyCharm's
  run configuration is injecting env vars instead.
