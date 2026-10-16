# Intent — per-role model stack and fallback ranks

**Status:** mostly built.
- **2026-09-18:** the decisions below came out of the FrankenLab hardware discussion.
- **2026-10-09:** the LLM gateway shipped (docs/llm-gateway.md). It does per-role models,
  fallback, timeouts and peak-diversion tagging.
- **2026-10-11/12:** measured on the real hardware (docs/heket-bench.md). The TTS role
  was added.
- **Still open:** hard spend caps, and the embedding / Uplink roles.

## Problem (as of 2026-09-18)

`app/llm.py` exposed one `get_chat_llm()` reading a single global `LLM_PROVIDER`, and
`graph.py` used it for both the agent and the classifier. `classify_topic` runs in front of
**every** user turn (`START → classify_topic → respond|decline`), so a full chat model paid
full latency to answer a binary on-topic question on every reply. There was no fallback:
an unreachable provider failed the turn outright.

## Desired outcome

Each role — chat, classifier, speech, and eventually embedding and Uplink — resolves to its
own provider and model, each with a **ranked list** of fallbacks, configured server-side.

The role split isn't only about cost. `docs/intent/agent-roles.md` argues the datarunner
wants a fundamentally smaller model than ALICE does, and that delegating the fetch loop to
it collapses large-model round trips — today the graph re-invokes the full chat model
after every tool result. That makes per-role selection a throughput lever on a
capacity-constrained GPU, not just a billing one.

## Current stack (2026-10-12)

The client asks for a **role**, never a model. The Hetzner server picks the backend
(`server/llm_gateway_service.py`, `server/tts_service.py`).

| Role | Primary | Fallback | Notes |
|---|---|---|---|
| `alice-chat` | gemma4 `dc35e8d9c606` on heket, thinking on | Claude Haiku 5.5 | Digest-pinned: a different build is treated as down |
| `alice-classify` | gemma4 on heket, thinking off | Claude Haiku 5.5 | Same loaded model as chat, so no extra VRAM |
| speech (`/tts`) | Kokoro `bf_isabella` on heket | ElevenLabs, only if a key is set | Different voice, so the fallback is optional |
| STT | Whisper `base` on the pilot's machine | — | Free, no server load, keeps audio off the server |

**Fallback triggers:**
- **Failure:** connect error, timeout (2 s connect, 30 s between streamed chunks) or a 5xx.
- **Peak diversion:** `overflow`, when heket already has `LLM_MAX_INFLIGHT=3` requests
  running.

Each LLM call writes an `llm_usage` row and each speech call a `tts_usage` row, with cost and
the fallback reason (docs/analytics.md).

## Decided

- **Chat runs local on heket.** *Built.* This is what drops marginal LLM cost per
  user to electricity, which is what makes a cheap subscription viable at all.
  - Measured: ~0.17 Wh, about €0.00006, per turn above idle.
  - Plus a fixed ~€20/month for the box being on (80 W idle).
- **~~Classifier stays on a cheap hosted model (GPT-4o-mini).~~** *Superseded 2026-10-09:*
  the classifier runs on heket's gemma4, with thinking off.
  - **VRAM wasn't the constraint it was expected to be.** gemma4 with 3 parallel slots uses 6.4
    GB of 16, and classify reuses the already-loaded model, so it costs no extra VRAM.
  - **gemma4 honours Ollama's JSON-schema `format`,** so its structured output is reliable.
  - **Keeping it local takes the per-turn hosted call off every reply.**
  - Not every local model is like this. gpt-oss:20b ignores `format` entirely
    (docs/heket-bench.md), so any candidate has to be checked on structured output.
- **Fallback ranks are ordered by cost-of-failure, not by quality.** *Built as one cheap
  rank:* Haiku 5.5, at ~$0.0002–0.0005 per call, $0.10/$0.50 per M tokens.
  - Haiku 4.5 was rejected at 10× the price and with worse tool use (33/45 vs 45/45).
  - No expensive rank 3 exists yet. A real outage is served by Haiku, and spend caps are what
    bound it.
- **A model must be qualified against tool selection before it enters a rank.** *Done for
  the current ranks* (evals/agent_eval, judge Haiku 5.5). Results on the full suite:

  | Model | Passed | Seconds per turn |
  |---|---|---|
  | gemma4 | 88% | 2.9 |
  | nemotron-3.5-lightning 30B | 74% | 47 |
  | qwen3.8 27B | 71% | 114 |
  | gpt-oss:20b | 67% | 3.4 |

  Only gemma4 earned the primary slot.
- **Planned peak diversion and failure fallback are distinct events, tagged at emit
  time.** *Built:* `fallback_reason = overflow` vs `down`/`timeout`/`5xx`/`digest_mismatch`.
  They're different beasts handled differently, and if they share a counter the "trending
  fallbacks" alert gets diluted by routine capacity management — paging on
  working-as-designed. See `docs/intent/observability.md` and `docs/analytics.md`.
- **Hard provider spend caps before there are users.** *Not built yet* — planned as
  `FALLBACK_DAILY_USD_CAP` (docs/llm-gateway.md, Limits). A runaway fallback can outspend a
  month of subscription revenue in a weekend. "ALICE is busy, try again shortly" is ugly
  and bounded; an uncapped key isn't.
- **Wait vs. spend under load: spend, but rarely.** *Decided 2026-10-12.* heket runs 3
  parallel slots (`OLLAMA_NUM_PARALLEL=3`), and a 4th simultaneous request diverts to Haiku.
  - **Three-way sharing slows turns** from 2.9 s to ~5.0 s. Haiku answers in ~4.3 s, so beyond 3,
    diverting is the faster option, not just the more expensive one.
  - **Diversion should be rare at launch scale.** Estimated at 10–20 pilots, assuming ~2.5% busy
    per active pilot: overflow is well under 1% of the time.
  - `inflight_at_start` on every `llm_usage` row replaces that estimate with real numbers.

## Hardware: heket (built 2026-10-08)

| Part | Spec |
|---|---|
| CPU | AMD Ryzen Threadripper 1900X, 8 cores / 16 threads |
| Board | MSI X399 Gaming Pro Carbon AC |
| RAM | 64 GB DDR4-2666 (4 × 16 GB) |
| GPU | NVIDIA RTX 4080, 16 GB, in PCI_E1 at PCIe gen3 x16 (the board's maximum) |
| Storage | 250 GB SATA SSD |
| OS / serving | Ubuntu 26.04, Ollama 0.40.2 (`KEEP_ALIVE=-1`, 32k context, 3 parallel slots), `kokoro-tts` service |
| Network | 1 Gb/s wired LAN; reached from Hetzner over Tailscale only (ufw allows 11434/8880 on `tailscale0`) |

**VRAM budget:**

| What | VRAM |
|---|---|
| gemma4 + 3 context slots | 6.4 GB |
| Kokoro | ~1.4 GB |
| **Total** | **~7.8 of 16 GB** |

That leaves headroom for a larger chat model, or for more slots.

This replaces the earlier plan: a laptop with the 4080 as an eGPU over OCuLink (PCIe 4.0 x4)
and 16 GB of system RAM. The RAM purchase it debated was made with the board: 64 GB.

## Constraints

- **The model must stay fully VRAM-resident.** The earlier reason was the OCuLink link, which no
  longer exists. Measured on the real box, the cost is the CPU and RAM side:
  - **nemotron 30B (48% on the CPU)** took 47 s per turn.
  - **qwen3.8 27B (36% on the CPU)** took 114 s per turn.
  - **Energy per turn rose 10–25×.** A model either fits in 16 GB or it isn't a candidate.
- **Kokoro belongs on the GPU.** On the 1900X's CPU it manages ~3× real time (0.7–6 s per reply),
  against 60–130 ms on the 4080. A CPU fallback on a separate machine would keep the same voice
  during outages, but the Hetzner web host (2 shared vCPUs) is too small for it.
- **Whisper stays client-side.** Free STT on the user's own machine, no server VRAM.
- ~~`with_fallbacks()` triggers on exception, so a slow provider never falls back.~~
  *Resolved by the gateway:* timeouts fall back (2 s connect, 30 s between chunks), and the box
  is benched for 30 s after a failure.

## Open questions

- **Larger chat model?** There is VRAM headroom (~8 GB free). Any candidate has to:
  - fit fully in 16 GB alongside Kokoro;
  - honour Ollama's `format`;
  - beat gemma4's 88% / 2.9 s on the eval.

  Measured so far, nothing does. MoE models with expert offload are ruled out by the
  constraint above.
- **Ollama vs. llama.cpp:** stays Ollama while nothing needs tensor-level placement. On the box,
  `KEEP_ALIVE=-1` keeps the model resident, so the cold-start concern behind `graph.prewarm()` /
  `PREWARM_OLLAMA` is now dev-only (`LLM_PROVIDER=ollama` on the Mac).
- **Timeout values in practice, and the spend-cap figures.** Tune them against `llm_usage` once
  pilots are active.
- **Time to first token** isn't recorded yet. It's the hardware-purchase trigger in
  `docs/intent/observability.md`; see the gaps in `docs/analytics.md`.
- **Embedding and Uplink roles:** no consumer yet.

## Cleanup while in here

`OLLAMA_EMBED_MODEL` and `OPENAI_EMBED_MODEL` in `.env-template` (commented out) are dead
config — embeddings went unused when the generic memory system was shelved. Either drop them
or give the embedding role a real consumer.
