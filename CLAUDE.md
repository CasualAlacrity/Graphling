# Graphling

## Purpose

A portfolio piece and a learning project for LangChain, LangGraph, LangSmith, and Pydantic. The
project is an AI companion — a persistent chat agent with a real memory system, not a toy chatbot.

v1 scope is intentionally narrow: one fixed persona, no multi-tenancy. The priority is a complete,
demoable application over breadth of features. Depth (a second persona, richer memory behavior)
is a "later, maybe" — don't build for it preemptively.

## Stack

- **LangChain** — LLM orchestration, structured output
- **LangGraph** — the conversational graph: extraction, recall, response nodes; checkpointer for
  per-thread short-term state
- **LangSmith** — tracing/observability across the pipeline (extraction call vs. chat call should
  be visible as separate traced steps)
- **Pydantic** — schemas for memory entries, structured LLM output
- **Chainlit** — chat interface. Chosen over Streamlit/Gradio because it's built for LLM chat
  apps specifically and handles async streaming and LangChain callbacks natively.
- **PostgreSQL + ChromaDB** — dual-write memory store (structured queries + semantic retrieval).
  See `lyra-memory-system-architecture.md` (in the user's Documents, not this repo) for the full
  design this is adapted from — ten memory classifications, weight/decay per classification,
  pinned/retrieved/scheduled three-layer retrieval, REPLACE-on-correction.
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
- **LangGraph's checkpointer handles short-term/thread state; the Postgres+Chroma system handles
  long-term memory.** These solve different timescales and both are needed — the checkpointer is
  not a substitute for the durable memory system.
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
