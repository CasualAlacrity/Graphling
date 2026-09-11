# Graphling

## Purpose

Started as a portfolio piece and a learning project for LangChain, LangGraph, LangSmith, and
Pydantic. As of 2026-09, also being taken seriously as a **prospective product / side hustle**:
ALICE is a voice copilot for Star Citizen whose real moat is a crowd-sourced, to-the-minute trade
data pool — pilots run ALICE, ALICE captures station buy/sell prices as they trade (OCR of the
commodity kiosk, planned), and that data is pooled server-side and shared to every ALICE user
instantly. UEX/StarHead impose a structural 24–48h trust-gated delay on new data, so ALICE can
contribute upstream freely while its users keep the freshness edge. Revenue comes from **selling
access to advanced ALICE features, not the data** — a subscription covering LLM/DB/service costs.
The learning-project framing still holds — the AI engineering is real and still the point — but
"complete, demoable application" is now also "something people could pay to use." See the
`project-product-thesis` memory for the go-to-market detail.

**Scope has widened accordingly.** Multi-tenancy is on the roadmap (it's the data-pool
foundation, not infra for two people — see `docs/todo.md` and the `project-multi-tenancy-scope`
memory). One fixed ALICE persona still holds; a second persona is still "later, maybe". Richer
per-value provenance/trust on the ledger is designed but **shelved until community features
(leaderboards) need it** — see `docs/ledger-trust-and-corrections.md`.

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
- **Chainlit** — secondary/legacy chat interface, kept working and documented but no longer the
  primary way to run the app. Chosen originally over Streamlit/Gradio because it's built for LLM
  chat apps specifically and handles async streaming and LangChain callbacks natively. The voice
  push-to-talk loop plus its `PTT_MODE=text` terminal-prompt fallback (see `app/voice/__init__.py`)
  is the primary interface now — it covers the same graph without a browser tab.
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
