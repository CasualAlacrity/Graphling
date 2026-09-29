# Intent — voice round-trip latency and in-flight feedback

**Status:** not started (2026-09-29). Per-utterance targets live in
`docs/utterance-coverage.md` → "Latency tiers — the guideline"; this doc is the why and
the approach.

## Problem

A pilot presses PTT, asks something, and hears nothing until ALICE has finished
*everything*. `app/voice/__init__.py` runs the turn fully serially:

1. Whisper transcribes the whole recording.
2. `graph.ainvoke()` waits for the complete final reply.
3. `voice/tts.py`'s `synthesize()` collects the **entire** ElevenLabs MP3 before returning.
4. `play_audio()` starts only then.

Nothing streams. A quick lookup and a heavy route search feel the same: silence, then an
answer — and for the heavy one the silence is long enough that the pilot can't tell
whether ALICE heard them.

We also can't say where the time goes. The harness's `latency_ms` wraps
`graph.ainvoke()` only; Whisper, TTS, and playback start are unmeasured, and so are the
stages inside the graph (classify, first model call, tool, second model call).

Model evidence so far (second machine, 5090, 2026-09-29, reasoning on — the
`OLLAMA_REASONING` default):

- **gemma4 8B:** ~2s per graph turn; 12/12, 11/12, 9/9, 8/9 across four runs.
- **gemma4:26b MoE:** ~10s average (one run, 9/9), individual cases 5–29s.
- **gemma4 12B:** 14–33s, and repeatedly skipped the tool call to state a fabricated answer
  (a different wrong Cutlass Black terminal each run); twice ran away to ~22k output tokens
  and ~195s. The split 8B-classify/12B-respond config inherited the 12B's failures.
- Every case sends ~20k input tokens (persona + 19 tool schemas).

## Desired outcome

- Every utterance meets its tier's budget, measured **PTT release → first ElevenLabs audio
  playing** — not graph time alone.
- A long request is never silent: the pilot hears ALICE acknowledge it quickly, and on long
  computes hears *true* progress as it happens.
- We know, per turn, how long each stage took — so targets and toggles are set from
  measured averages, not guesses.

## Decided

- **Round trip is the unit of measure** (Jeff, 2026-09-29): Whisper start → ElevenLabs
  stream starting. Graph-only timing is a lower bound and is labelled as such wherever it's
  reported.
- **Budgets are per tier, not one number.** "Where can I rent a Hull B?" must be faster than
  "find me the best route"; a single 4s target is either too loose for lookups or
  impossible for route search. Longer total time is acceptable *if* feedback covers it.
- **Stage timing comes first.** Without per-stage numbers, every other change here is
  guesswork about which stage to attack. Stages: Whisper, classify, respond #1 (to tool
  call), tool execution, respond #2 (to final text), TTS time-to-first-byte, playback start.
- **Heavy-compute acks confirm the request** (Jeff, 2026-09-29). e.g. "Find me the best
  route near Crusader in my Conny" → *"Searching for the best routes near Crusader in your
  Constellation Taurus. This will take a sec — grab a Cruz."* Two jobs at once:
  - **Covers the wait.** That line is ~5s of speech — half a 10s search spent listening,
    not waiting, so the final answer feels faster.
  - **Confirms what ALICE understood.** Whisper has already mangled ship names ("my
    railing" → Railen), and "Conny" could be any Constellation variant. Echoing the
    *resolved* entities lets the pilot correct a wrong ship in the first second rather
    than after a full search on it.
- **The ack echoes resolved values, so it fires from inside the tool** — after the tool's own
  entity resolution, not from the model's raw tool-call args ("Conny"). That makes it the
  tool's first real stage update, true by construction.
- **Ack text is a filled template, never generated.** Hand-written in-persona variants per
  tool ("Searching for the best routes {near/in} {origin} in your {ship}."). No extra model
  call, nothing to hallucinate, text available instantly.
- **Ack audio = a dynamic head + a pre-recorded tail.** The head (the part with entity names)
  goes through TTS; the flavour tail ("This will take a sec — grab a Cruz.") is rendered
  once and cached like any fixed line. Stitching pre-rendered *name* fragments into
  sentences is rejected — choppy prosody, and hundreds of ships × locations.
- **Tier-1/2 tools get plain pre-recorded acks or none** — their answer arrives fast enough
  that it *is* the first audio. The `progress_label` every tool already carries
  (`uplink_tool.py`) is the natural hook; nothing consumes it today.
- **TTS audio is cached lazily, never pre-rendered** (Jeff, 2026-09-29). A clip is paid for
  the first time any pilot triggers it and served from the library after that; a
  combination nobody asks for costs nothing. Pre-rendering everything was rejected: ~400
  origins × ~70 cargo ships × 3 modes ≈ 84k clips ≈ 5M ElevenLabs characters, mostly for
  pairs no pilot will ever fly. Demand is heavily skewed (Orison + Railen), so a lazy
  library stays small — likely hundreds of MB; even the full theoretical set is ~4 GB at
  `mp3_44100_128`, ~1 GB at `mp3_22050_32`.
- **Cache key is the rendered content, not entity IDs:** a hash of (text, voice_id,
  model_id, voice settings, output_format). An ID-shaped path
  (`near_sys[id]_ship_[id].mp3`) silently serves stale audio after a template reword, a CIG
  rename, a voice change, or a pronunciation fix; a content hash makes every one of those a
  new key automatically, and any tool that says the same sentence shares the clip. An
  ID-based index can sit alongside for browsing, but it isn't the key.
- **Lookup order: pilot's machine → shared library → ElevenLabs.** A pilot flies the same
  ship from the same few origins most sessions, so a local disk cache hits nearly always —
  zero cost, zero network. The shared library (NAS for the seed-user phase) catches
  cross-pilot repeats. Storage sits behind a small interface so the NAS can become object
  storage (S3/R2) when residential upload and uptime stop being enough.
- **An unresolved entity always means clarify, never guess** (Jeff, 2026-09-29). If the
  tool can't pin an entity down, ALICE asks before searching — "Can you tell me the ship
  again? I didn't get it." when there's no candidate, or "I didn't catch 'Conny' — did you
  mean the Constellation Taurus?" when there's a best match. Once the pilot confirms, the
  confirming ack follows: "Got it — searching near Crusader in your Constellation Taurus."
  Guess-and-let-them-correct was rejected: a wrong search is ~10s wasted, and the pilot
  can't currently interrupt one (see open questions). Note the pilot's "yes" is a
  pending-confirmation turn — exactly the shape `classify_topic` has already misfired on
  ("Let's do it.", todo Phase 3 seed #4), so this depends on that being fixed.
- **Voice lines never overlap or cut each other off** (Jeff, 2026-09-29). A line in progress
  — ack, stage update, or answer — finishes before the next one starts, with a short gap
  between them for comprehension (a few hundred ms; tune by ear once it's running).
- **Stream TTS playback.** Play ElevenLabs audio as chunks arrive rather than after the
  full MP3 downloads.
- **Stage updates for long computes, reporting real stages only.** e.g. "Checking prices out
  of Orison…" → "Ranking forty routes…". Timed filler ("almost done") is rejected: a
  progress line that turns out false trains pilots to ignore ALICE's progress lines — the
  same trust failure as a bad shared price, smaller scale.
- **Stage updates are toggleable at two levels:** by us per tool, switched on only where
  measured averages show a pilot would otherwise wait in silence; and by the pilot, who may
  not want to hear them. Acks and final answers are not optional.
- **12B rejected, including as a conversation-only model** (2026-09-29). It fabricated
  answers instead of calling tools, was the slowest config tested, and its toolless
  answers were judged wrong more often than the 8B's. A "8B picks the tool, 12B phrases the
  answer" split also *adds* a model call to every turn — the opposite of this doc's goal.
  If ALICE's voice feels thin on the 8B, fix the prompt before reaching for a bigger model.

- **Audio vocabulary: earcons for the routine, speech for meaning** (2026-09-29). Modelled
  on Star Trek's ship computer (chirp for simple commands, "Working…" for longer ones,
  "Unable to comply" for failure) and aviation callouts (identical every time — the
  sameness is the point). Backed by research: speech beats earcons for conveying meaning,
  earcons work as confirmations in repetitive contexts, and a set should stay small
  ([NN/g](https://www.nngroup.com/articles/audio-signifiers-voice-interaction/);
  [2023 meta-analysis](https://www.tandfonline.com/doi/full/10.1080/25742442.2023.2219201)).

  | When | ALICE plays |
  |---|---|
  | PTT pressed | "listening" chirp (optional) |
  | Quick lookup, still waiting after ~1s | "working" chirp, then the spoken answer |
  | Ledger milestone with no stated values ("Landed", "Loaded") | "logged" chirp + overlay update |
  | Ledger write with stated values ("Bought 640 at 14.2") | short spoken readback: "640 at 14.2, logged." |
  | Heavy compute | confirming ack (above) |
  | Unresolved entity | spoken clarification (above) |
  | Failure | error tone + a short spoken reason |

  Three or four earcons total, shipped with the client as local files — zero ElevenLabs
  cost. This also mostly dissolves the ack-variety problem: earcons cover the
  high-frequency cases, and confirming acks vary naturally with their entities.
- **Stated numbers are always read back** (Jeff, 2026-09-29). A chirp can't tell the pilot
  whether ALICE heard 640 or 614; "640 at 14.2" lets them fix it immediately, while the
  numbers are still in their head. **Depends on a correction path that doesn't exist yet**
  — see Constraints.
- **Earcons are ALICE's own.** No Star Trek or Star Citizen sounds (Paramount's and CIG's
  IP); the chirp set is part of ALICE's identity the way the chirp is the Enterprise
  computer's.

## Constraints

- Whisper stays client-side (`intent/model-stack.md`) — its speed depends on the pilot's
  hardware, not ours. Currently `base`, `fp16=False`.
- Graph nodes, tools, prompts, and schemas are Jeff's to write
  (`feedback-ask-before-editing`); Claude sketches shapes and can build the timing
  instrumentation/Qt-side plumbing when asked.
- **A readback is only useful if the pilot can act on it — and today they can't**
  (checked 2026-09-29). Once `record_purchase`/`record_sale` runs (overlay or AI tool), the
  server refuses a second record and has no update endpoint; the overlay's Mark Done and
  Finalize Run views are read-only recaps; Abandon disappears once anything is bought.
  So "no, 614" after a readback has nowhere to go. Needs: an edit path for recorded leg
  values up to Finalize (overlay + server), and a voice correction tool on top of it
  (designed in `ledger-trust-and-corrections.md`, not built).
- Information-not-decisions still applies to progress lines: they report what ALICE is
  doing, never a premature conclusion.
- No markdown or raw-digit reformatting may reach TTS (harness evaluator (e), not built).
  Streaming makes this harder to catch, not easier.

## Open questions

- **Reasoning off.** The 26B's output was mostly thinking tokens around one-sentence
  replies. Rerun 8B and 26B with `OLLAMA_REASONING=false` before concluding anything
  about whether 26B can fit tier budgets — and check whether accuracy holds.
- **The 26B's 26–29s outliers** had *fewer* output tokens than its 10s cases. Prompt eval?
  Cache eviction? Unexplained until stage timing exists.
- **Ack timing depends on respond #1.** An ack fired on tool-call emission still waits for
  Whisper + classify + the first model call. Is that under 2s? If not, is there a safe
  earlier trigger — without acking something ALICE then declines?
- **Streaming LLM tokens into TTS** (sentence-chunked) — worth it once TTS playback streams?
  Interacts with tool-call detection and formatting cleanup.
- **How stage updates reach the voice loop from inside a tool.** Likely the same
  display-intent path as `docs/presence-layer.md`, with an audio consumer alongside Qt.
- **Ack variety.** The same line fifty times a night sounds robotic, but every alternative
  wording is a different sentence, so a separately paid-for and cached clip. Leaning: keep
  the name-bearing head to one wording per tool and vary only the pre-recorded tail ("grab
  a Cruz" / "won't be long" / …) — variety costs a handful of fixed clips, not a
  multiplier on every ship × location pair.
- **Ambiguous entities in the ack.** If "Conny" can't be resolved to one variant (no hangar
  API yet — see utterance-coverage "Parked"), does the ack state its best guess and let the
  pilot correct it, or ask before searching?
- **ElevenLabs terms on storing and redistributing generated audio.** Paid plans are
  believed to grant commercial use of output, but serving one pilot's cached clip to
  another makes it part of the product — read the current terms before the shared
  library ships, alongside the CIG ToS read.
- **Tier 0 needs a no-model path** ("say that again", "stop") — currently every utterance
  goes through classify + respond.
- **Where the pilot's feedback toggle lives.** Real users beyond the seed tester need a
  distributable client, which will have a small settings GUI (Jeff, 2026-09-29) — the
  toggle belongs there. Still open: stored locally only, or synced to their server profile
  so it follows them across machines.
- **~20k input tokens per turn** — tool-schema bulk is a latency lever on every tier
  (already noted in `todo.md` Phase 3).
- **Interrupting ALICE (barge-in).** The confirming ack only helps if the pilot can act on
  it. Today the loop only listens again after playback finishes, so "no, the Andromeda"
  can't cut in and a wrong search runs to completion. Needs PTT during speech/search to
  stop playback and cancel the in-flight tool.
- **Purging a bad clip.** If ElevenLabs mispronounces a name once, the lazy cache serves
  that mistake to every pilot indefinitely. The content-hash key covers changed text or
  voice settings, but not a bad render of unchanged text — needs a way to flag and delete
  (or force re-render of) a specific clip.
