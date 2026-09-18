# Intent — agent roles: ALICE, Uplink, and Auspex

**Status:** decided 2026-09-18. ALICE exists. **Uplink stays a layer, not an agent** — see
the verdict below. **Auspex** (the OCR agent) isn't built and is the only genuine second
agent planned.

## Origin

Jeff's mentor suggested the product should be multi-agent, specifically that there should
be an agent for RAG. In this system "RAG" is really tool-mediated API calls — there's no
vector retrieval — so that agent became **Uplink**: a datarunner that would check the
cache, judge whether the data was fresh enough, hit UEX only when it wasn't, and hand
structured data back for ALICE to voice.

Recorded because the naming was later misread as inconsistency and nearly "cleaned up,"
erasing a deliberate distinction that had never been written down.

## Verdict: Uplink is a layer, not an agent

**The tools already do Uplink's job, and they do it deterministically.** `find_best_route`
fans out with `asyncio.gather`, ranks by profit per hour, patches autoload. `best_route_tool`
performs six resolution and API operations before it returns. That's the datarunner role,
implemented as tested code.

Promoting it to an agent would be a regression:

- **It moves solved problems back into model space.** The resolution chain,
  `resolve_or_hedge`'s confidence hedging, region resolution — all deterministic and
  testable today. An agent deciding "do I need orbits?" reintroduces the failure modes
  that work eliminated.
- **The freshness judgment is arithmetic, not judgment.** "Is this entry older than the
  volatility-scaled threshold for this terminal?" is a rule, and it's the same volatility
  bound already decided for OCR validation. It doesn't want an LLM.
- **Every Uplink call would be extra inference on the pilot's critical path.** If it isn't
  saving a larger model pass, it's pure latency in front of someone waiting to hear an
  answer.

**Explicitly retracted** (this doc claimed it on 2026-09-18 and it was wrong): that an
Uplink agent would collapse large-model round trips. The graph does re-invoke the chat
model after every tool result, but **most turns are a single fat tool call**, so there are
no round trips to collapse. That argument holds for a system of many thin API-specific
tools; this isn't one, and moving to thin tools would cost more than it saved for the
reasons above.

Uplink earns agent status later only if genuine planning problems appear — compound
queries where the API sequence isn't predetermined. Not before.

## What was actually wrong: the tools are doing ALICE's job

The real finding behind the unease. **Every tool returns a prose f-string; nothing returns
structured data.** The tools write ALICE's sentences and she relays them — so the mediator
isn't mediating, she's a passthrough for text the data layer authored.

The `route_token` mechanism is the tell: `best_route` appends *"(Internal note, don't say
this part aloud: route_token=…)"* because prose is the only channel it has for passing a
value to the model.

**The fix is structured returns, and it needs no agent:**

- Tools return structured results (`TradeRouteData` or similar); ALICE transforms them
  into voice. That is genuinely her role.
- The presence layer requires this independently — `docs/presence-layer.md` already states
  display intents must carry structured fields, "not the text string those tools return
  today."
- `route_token` becomes a field rather than a parenthetical the model is instructed not to
  read aloud.
- Tool output becomes testable as data instead of by string matching, which matters given
  how little coverage that layer has.

## Auspex — the one genuine agent

Deliberately narrow, running its own loop: take an image off the queue, extract, write the
result into the cache DB, and possibly push upstream to StarHead/UEX. Nothing else. Full
design in `docs/intent/ocr-trade-data.md`.

> **The tools withdraw; Auspex deposits.**

The cache is the boundary, and it's the *only* coupling. **ALICE never knows Auspex
exists** — different machine, different pipeline, no call between them in either
direction. Auspex writes rows; ALICE's tools read rows; neither has a reference to the
other. Auspex never answers a question, and nothing on the request path ever processes an
image.

That buys three things for free:

- **Graceful degradation.** Auspex can be down entirely and ALICE keeps working, serving
  cached and UEX data as before. The pool stops getting fresher; nothing breaks.
- **Independent change.** Auspex can be restarted, moved, or rewritten without touching
  ALICE. The only contract between them is the cache schema.
- **No distributed-systems problem.** No RPC, no message bus, no partial-failure handling
  between two live services — just one writer and one reader over a database.

**The cost of that decoupling is the blind spot**: because ALICE can't tell whether Auspex
is healthy, degraded extraction is invisible from the pilot's side — data simply gets
quietly staler or quietly wrong. That's exactly why extraction metrics are load-bearing
rather than nice-to-have (`docs/intent/observability.md`), and why the decoupling is worth
keeping rather than "fixing" with a health handshake.

If pilots ever need telling that Auspex is struggling, **that goes to Discord, not through
ALICE** — a status bot posts it (see `docs/intent/observability.md`). Routing it through
her would hand her awareness of infrastructure she has no business having, and an
assistant apologising for backend problems is a persona break, not a feature.

**Why it's a separate agent when Uplink isn't:**

- **It has its own loop and no caller.** It isn't invoked by a turn — it works a backlog on
  its own schedule. That's what makes it an agent rather than a function someone calls.
- **Its latency profile conflicts with the request path.** Chewing a backlog would delay
  answers to the pilot, which is also why its GPU is separate from companion inference.
- **It genuinely needs model judgment** — reading a semi-opaque, parallax-shifted game UI
  is the one job here that classical code demonstrably can't do (tested at ~80%).

It also wants a **fundamentally smaller model than ALICE** in its extraction role: narrow,
mechanical, with checkable right answers rather than questions of voice or tone. Same
reasoning already applied to `classify_topic` — see `docs/intent/model-stack.md`.

## Naming consequences

- **Keep `UplinkTool` / `UEXBackedTool`.** The name correctly describes the data layer,
  which is what Uplink always was in practice. Every `uplink` reference under `app/tools/`
  is right where it belongs.
- **Scrub Uplink from the user-facing surface only.** `app/voice/__init__.py` labels
  ALICE's own spoken reply as Uplink (`print(f"[Uplink] Uplink: {response_text!r}")`) and
  `tts.py` calls ALICE's TTS "Uplink's voice." The package is `name = "uplink"` with
  `uplink-*` entry points, though the app a pilot launches is ALICE. Surgical fix, not a
  sweep — an earlier suggestion to rename everything to ALICE would have flattened the
  correct half to fix the leaked half.
- **Auspex is the OCR agent's name** (chosen 2026-09-18). A sensor device that reads its
  surroundings and reports what it finds — which is the job. It earns a name because it's
  the only component that's genuinely an autonomous actor rather than a layer or a
  function. "OCR pipeline" still refers to the processing path; **Auspex** is the thing
  running it.

## Open questions

- Whether the structured-return refactor lands before or after the API tier. It touches
  every tool, so it's cheaper to do before more tools exist, and it unblocks the presence
  layer either way.
*(Resolved 2026-09-18: Auspex never surfaces to pilots and ALICE has no awareness of it —
see the decoupling note above. It's visible only to Jeff and moderators through the
Discord review channel, so it needs no persona.)*
