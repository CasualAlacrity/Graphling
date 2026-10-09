# Voice stage timing — measuring where a turn's time goes

**Status:** built (2026-10-01) — `app/turn_metrics.py`'s `TurnTimingsCallback` (renamed `TurnMetricsCallback` 2026-10-06, when it also took over per-call token/cost capture -- see `tests/test_turn_usage.py`; log is now `turn_metrics.jsonl`, path override `ALICE_METRICS_LOG_PATH`), wired into
both `voice/__init__.py` and `evals/agent_eval/run.py`, 11 new tests, full suite passing.
Confirmed live against the real graph (not just mocked): `metadata["langgraph_node"]` is
genuinely how LangGraph tags each node's LLM calls (verified directly before writing the
handler, not assumed), and a real harness run against local Ollama showed `classify_ms` +
`respond_ms` summing almost exactly to the already-existing `latency_ms` on every case —
including a turn whose respond↔tools round trip correctly produced two separate
`respond_ms` entries. One real addition beyond this doc's original stage table:
`decline_ms` (the `decline` node's `reject_llm` call) — a real, measurable stage that was
otherwise silently missing from any declined turn's timings. First item under "Decided" in
`docs/intent/voice-latency.md` ("Stage timing comes first"): every other latency change
there is guesswork until this exists. Targets it measures against: the latency tiers in
`docs/utterance-coverage.md`.

**Owner:** Claude — this is instrumentation, not graph/tool logic. It must **not** change
graph behaviour, node code, prompts, or tool code (those are Jeff's). Observing via
callbacks and timing around existing calls only.

## Goal

For every live voice turn, record how long each stage took, from PTT release to the first
audio playing, so tier budgets and feedback toggles can be set from measured averages.

## Stages

| Stage | Where it's measured |
|---|---|
| `whisper_ms` | around `_model.transcribe(...)` in `voice/voice_input.py::transcribe` — transcription only, not recording time |
| `classify_ms` | `classify_topic` node |
| `respond_ms[]` | each `respond` LLM call — a list, a turn can have several respond ⇄ tools round trips |
| `tool_ms[]` | each tool execution, with the tool name |
| `graph_ms` | wall clock around `graph.ainvoke()` (what the harness's `latency_ms` already measures) |
| `tts_first_chunk_ms` | from calling ElevenLabs to the first audio chunk arriving, inside `voice/tts.py::synthesize` |
| `tts_total_ms` | until the full MP3 is collected (today playback waits for this) |
| `round_trip_ms` | PTT release → `play_audio` starting — **the number the tier budgets are about** |

`tts_first_chunk_ms` is measurable today even though playback doesn't stream yet; the gap
between it and `tts_total_ms` is exactly what streaming playback would win back.

## Approach

- **In-graph stages via a LangChain callback handler**, passed through the `config` already
  given to `graph.ainvoke()` in `voice/__init__.py`. Chain/LLM/tool start and end events
  give node, LLM-call, and tool timings without touching node code.
- **Out-of-graph stages** (Whisper, TTS, playback start) by timing around the existing
  calls. `transcribe()` and `synthesize()` return their duration alongside their result,
  or record into a small per-turn timing object — whichever keeps their call sites
  simplest.
- **A `TurnTimings` record** next to `TurnMetrics` in `app/turn_metrics.py` — same
  philosophy: pure data, callers decide what to do with it. Include the tool names called,
  so a turn can be bucketed into its latency tier.
- **Output:** one compact line printed per turn in the voice loop's existing `[ALICE]` log
  style, plus one JSON line per turn appended to a local file (path via env var, with a
  default under the user's app data rather than the repo). Nothing sent to the server.
- **Harness:** `evals/agent_eval/run.py` passes the same callback handler, so each report
  records `classify_ms` / `respond_ms` / `tool_ms` per case — explains outliers like the
  26B's 26–29s cases, which had fewer output tokens than its 10s ones.

## Tests

- Callback handler: given a fake sequence of start/end events, produces the right
  per-stage durations, including multiple respond/tool calls in one turn.
- `TurnTimings` serializes to one JSON line and round-trips.

## Out of scope

Streaming playback, pre-recorded acks, earcons, stage-update voice lines — all later items
in `docs/intent/voice-latency.md`, and they need these numbers first.

**Also deferred (confirmed 2026-10-01, not now):** sending these logs to a server-side
analytics DB on the Hetzner box and an "ALICE health" Grafana dashboard on top of it.
The only thing a server-side copy buys over today's local-only file is fleet-level
visibility across many pilots, which doesn't exist yet with one real user — revisit once
real multi-pilot usage does. Likely reuses the existing Hetzner Postgres (one new table)
and the same best-effort client pattern already established for other server calls.
