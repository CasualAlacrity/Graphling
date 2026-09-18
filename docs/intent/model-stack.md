# Intent — per-role model stack and fallback ranks

**Status:** not started (2026-09-18). Decisions below came out of the FrankenLab hardware
discussion; nothing is built yet.

## Problem

`app/llm.py` exposes one `get_chat_llm()` reading a single global `LLM_PROVIDER`, and
`graph.py` uses it for both the agent and the classifier:

```python
llm = get_chat_llm().bind_tools(tools)
classifier_llm = get_chat_llm().with_structured_output(TopicClassification)
```

Same model for both. But `classify_topic` runs in front of **every** user turn
(`START → classify_topic → respond|decline`), so a full chat model pays full latency to
answer a binary on-topic question, and that lands on every single reply.

There's also no fallback of any kind: if the configured provider is unreachable, the turn
fails outright.

## Desired outcome

Each role — chat, classifier, embedding, and eventually Uplink — resolves to its own
provider and model, each with a **ranked list** of fallbacks, configured per install.

The role split isn't only about cost. `docs/intent/agent-roles.md` argues the datarunner
wants a fundamentally smaller model than ALICE does, and that delegating the fetch loop to
it collapses large-model round trips — today the graph re-invokes the full chat model
after every tool result. That makes per-role selection a throughput lever on a
capacity-constrained GPU, not just a billing one.

## Decided

- **Chat runs local on FrankenLab.** This is what drops marginal LLM cost per user to
  electricity, which is what makes a cheap subscription viable at all.
- **Classifier stays on a cheap hosted model (GPT-4o-mini).** Clearest "don't self-host
  this" case in the system: the work is tiny, structured output is where small local
  models are least reliable, and keeping it off the GPU leaves all 16 GB of VRAM for the
  chat model, which is the piece that actually benefits from being local. The current
  classifier was built knowing a smaller model was coming and was cheap to leave rough
  because the LLM calls ran on a school API key — neither condition holds now.
- **Fallback ranks are ordered by cost-of-failure, not by quality.** If rank 2 is a
  frontier model, every capacity overflow costs premium. Cheap at rank 2, expensive at
  rank 3: routine overflow degrades quality slightly and stays affordable, and only real
  outages reach the costly tier.
- **A model must be qualified against tool selection before it enters a rank.** With 18
  tools bound, a model that converses well but mis-picks tools is *worse* than one that's
  unavailable — it takes confident wrong actions on a live trade run. This is a second job
  for the Phase 3 harness: not just evaluating upgrades, but earning a model its place in
  the ranking.
- **Planned peak diversion and failure fallback are distinct events, tagged at emit
  time.** They're different beasts handled differently, and if they share a counter the
  "trending fallbacks" alert gets diluted by routine capacity management — paging on
  working-as-designed. Tagging costs nothing while writing it and is painful to untangle
  afterwards. See `docs/intent/observability.md`.
- **Hard provider spend caps before there are users.** A runaway fallback can outspend a
  month of subscription revenue in a weekend. "ALICE is busy, try again shortly" is ugly
  and bounded; an uncapped key isn't.

## Constraints

- **`with_fallbacks()` triggers on exception**, so an unreachable provider falls back
  correctly but a *slow* one (alive, 30 s, no error) never does. Needs a timeout, or the
  fallback misses the failure mode users actually notice.
- **16 GB VRAM, 16 GB system RAM** (64 GB deferred — RAM is ~$300-500 per 16 GB stick at
  current prices, and the cheap step is +16 GB into the empty second slot, not the ~$1,300
  jump to 64 GB).
- **The model must stay fully VRAM-resident.** OCuLink is PCIe 4.0 x4, roughly a quarter
  of a desktop x16 slot. Irrelevant while weights stay on the card; brutal the moment
  anything streams across it.
- **Whisper stays client-side.** Free STT on the user's own machine, no server VRAM.

## Open questions

- **Benchmark before buying anything.** A small MoE (the ~30B-total / ~3B-active shape)
  quantised to fit entirely in 16 GB VRAM never touches system RAM or OCuLink, and gets
  MoE's speed benefit from low active-parameter count. If it meets the latency and quality
  bar against Gemma 4, the RAM purchase defers indefinitely. MoE *with expert offload* is
  the configuration to avoid here — it's the one case where the x4 link is the bottleneck.
- Ollama's fine-grained MoE offload control is weaker than llama.cpp's. If the winning
  configuration needs tensor-level placement, that may mean dropping below Ollama — which
  matters because `get_chat_llm()` is `ChatOllama`-shaped.
- Timeout values, and the spend-cap figures.
- **Wait-vs-spend under load is a product decision, not a retry detail.** Today the design
  would silently choose "spend money" over "make users wait." At 2 users that's invisible;
  it should be chosen deliberately before it isn't. Current lean: queue locally and accept
  a second or two of degradation, since that's quality loss rather than failure — see the
  scale discussion in `docs/presence-layer.md` about making a wait visible.

## Cleanup while in here

`OLLAMA_EMBED_MODEL` and `OPENAI_EMBED_MODEL` in `.env-template` are dead config —
embeddings went unused when the generic memory system was shelved. Either drop them or
give the embedding role a real consumer.
