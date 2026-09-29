# Voice stage timing — measuring where a turn's time goes

**Status:** proposal, ready to build (2026-09-29). First item under "Decided" in
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
