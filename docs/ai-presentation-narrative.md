# AI presentation narrative — working draft

**Status:** brainstorm in progress, expect this to keep changing over the next week or so.
Captures what's been decided so the shape doesn't have to get re-derived from scratch each
session. Nothing here is built yet — see "What each beat requires" for the actual engineering
gap per beat.

## Identity

- **Name: ALICE** — *Accidental Logistics, Intelligence & Coordination Engine*. Replaces the
  placeholder name "Uplink" currently in `prompts/persona.md`. Not yet renamed in code — pending
  the "how much backstory lives in the system prompt vs. stays narration-only" decision below.
- **Tagline:** "Industrial intelligence. Accidental ingenuity." — reframes "accidental" as the
  source of the ingenuity, not just a joke about the org's origin story.
- **Org context:** built for [Accidental Industries \[ACCIDENTAL\]](https://robertsspaceindustries.com/en/orgs/ACCIDENTAL)
  — a small (currently 2-member listed, but the user regularly flies with up to 3 others, so up
  to a 4-person crew on a good night), casual, self-aware org. Their own charter: *"Founded
  entirely by accident. Expanded through questionable decisions. Sustained by friendship,
  engineering and industrial-scale optimism."* Focus: mining, salvage, hauling, manufacturing —
  "if it can be mined, salvaged, hauled, or accidentally exploded, we're interested."
- **Voice design decision:** keep the existing persona voice (blunt, dry, economical, "someone
  not something," no filler/sign-offs — see `prompts/persona.md`) unchanged under the new name.
  The comedic engine of the show is the *contrast* between a serious, no-nonsense AI and an org
  that openly admits it runs on questionable decisions — not a rewrite of ALICE's tone to match
  the org's own casualness. Confirmed working example: the "firework display" line below is dry,
  not goofy, and that's what makes it land.
- **Open decision:** does the ALICE backstory (backronym, org affiliation, tagline) go **into the
  system prompt** so she can answer "what does ALICE stand for?" in character if asked live, or
  does that copy stay presentation-only (slides/narration) with the prompt just renamed? Leaning
  toward this being real persona-content work the user writes rather than a plain find-and-replace,
  per the project's usual AI-engineering-logic split — not decided yet.

## Hard constraint for the demo

**Solo pilot only during the actual presentation.** Any crew reference is narration/flavor in the
dialogue, never a live second user. This directly avoids reopening the "no multi-tenancy /
single implicit user for v1" decision already made in `CLAUDE.md` — the crew is a detail in what
ALICE *says*, not something the app tracks or models.

## Slide deck — working draft

**This section is the actual pasteable content for the Google Slides deck** (audience: AI
Engineering students — a technical peer audience, so the engineering war-stories carry as much
weight as the demo itself). It was drafted slide-by-slide earlier in the same session that built
most of the features it describes, lived only in chat history, and had already survived one
context-compaction before landing here — moved into the repo so it stops being one compaction away
from gone. Slide numbers are provisional; expect reordering once this goes into Slides proper.

1. **Title** — ALICE / Uplink, tagline, one line on what it is (AI companion + trade-run tracker
   for Star Citizen).
2. **What & why** — learning project for LangChain/LangGraph/LangSmith/Pydantic; deliberately
   narrow scope (one game, one persona, no multi-tenancy) because depth beats breadth for proving
   an idea works.
3. **Architecture at a glance** — `classify_topic → respond ⇄ tools`, Chainlit + voice as two
   interfaces over the same graph, Postgres for trade-run state, LangSmith Hub serving the
   persona/classifier prompts live (not hardcoded). Aside worth a line: **Thread vs. Run** — a
   thread (`thread_id`, `MemorySaver`) is the durable conversation a pilot is having with ALICE;
   a run is one `graph.ainvoke()` cycle within it (one LangSmith trace). Cleanly separates "state
   that persists" from "one unit of execution," which maps directly to the `CLAUDE.md` decision
   that the checkpointer and long-term persistence are separate concerns.
4. **The guardrail (as built)** — a dedicated `classify_topic` node runs before `respond` on every
   turn, its own structured-output call (`TopicClassification`: `on_topic`, `decline_line_str`,
   `reason`), not a keyword filter. Decline lines come from the same Hub-managed prompt that holds
   the persona — content and behavior aren't split across two places.
5. **Build vs. buy — what LangChain actually ships.** Checked the primary source before assuming:
   no off-the-shelf topic-classification middleware ships with LangChain. Two built-ins exist —
   `PIIMiddleware` and `HumanInTheLoopMiddleware` — both solving a different problem than
   domain/scope enforcement. LangChain's own docs *do* show the pattern for building exactly this
   kind of guardrail: a `@before_agent` hook that runs ahead of the model call and can
   short-circuit the turn — structurally the same idea as `classify_topic`, just a graph node
   instead of a decorator because this app is a hand-built `StateGraph`, not `create_agent()`.
   NeMo Guardrails (`RunnableRails`) is the closest pre-built topic-rail system, but it's a whole
   second framework with its own Colang DSL. Takeaway: not reinventing something that already
   existed — independently arriving at the same pattern LangChain's own docs recommend for this
   exact case.
6. **Tool-calling at scale — 17 tools, three families.** UEX market lookups (read-only), trade-run
   actions (state-mutating), general tools (timers, travel time, best-route — reusable outside
   trade-run context entirely). Layering rule worth naming: tool code never imports from the UI
   layers, so the same 17 tools work identically whether the pilot's typing in Chainlit or talking
   over voice.
7. **Tool selection: the literature, and where we actually stand.** Agent tool accuracy degrades
   measurably past **10–15 tools**; production systems show a clear drop **crossing 15–20 in
   active rotation** (OpenAI's hard ceiling is 128, degradation starts well before it). Full tool
   definitions alone eat **5–7% of context** before the user's message arrives. Two named failure
   modes: **"lost in the middle"** (the right tool sits in a context dead zone the model
   structurally underweights) and **tool hallucination** (attention spreads across similar-sounding
   tools — invents a name, or calls a real tool with another tool's arguments). The literature
   names six mitigations — gating, retrieval-based selection, semantic routing, planner-based
   decomposition, confidence-tiered fallback, empirical benchmarking; RAG-MCP's retrieval approach
   alone took tool-selection accuracy from **13.6% to 43.1%** while cutting prompt tokens over
   50%. LangChain ships one of the six, off the shelf — `LLMToolSelectorMiddleware`, an LLM call
   with structured output that narrows the tool list before the main model sees it — a different
   cost profile than embedding retrieval (an extra model call per turn), and the only one of six
   strategies provided. **Graphling: 17 tools, no selection logic, past every threshold cited.**
   The diagnosed failures so far were persona-framing bugs, not volume-confusion bugs — hasn't
   visibly bitten yet, but it's the next lever to pull, not a hypothetical one.
8. **War story #1 — a good tool description still didn't fire.** `mark_arrived`'s description
   almost verbatim matched what the pilot said. Verified via LangSmith trace, not a guess, that
   the tool schema itself was clean. Root cause: the persona prompt's overall framing never
   established that *acting* on the pilot's behalf was in scope at all — it read as an
   information-lookup assistant. Fix was in the persona, not the tool: "when the pilot reports a
   milestone has happened... call the matching tool rather than answering from memory." Lesson:
   tool selection isn't just a function of the tool's own description — it's gated by whether the
   system prompt's self-concept for the agent includes that class of action at all.
9. **War story #2 — the hallucinated destination.** "Cargo's loaded, what's my destination"
   produced "Admin at GrimHEX, in orbit around Yela" — fabricated; the real destination was a
   different star system entirely. Traced the text back to its source: lifted verbatim from the
   persona prompt's own *worked examples* (illustrative format samples, never real data), because
   no tool existed that could answer "what's the current destination," so the model pattern-matched
   to the nearest thing in its own context window. Fix was two-part: built `trade_run_status` so
   there was a real source of truth, and tightened the persona ("never assume or invent a trade
   run's current state," examples "describe format and phrasing only, never real data"). Lesson:
   hallucination isn't always fabrication from nothing — sometimes the model retrieves from the
   wrong place in its own context, and a system prompt's few-shot examples can themselves be the
   leak vector.
10. **War story #3 — trust one data point, get burned twice.** Built travel-time estimation on an
    undocumented API (`star-citizen.wiki`'s `locations/positions` endpoint, found by watching
    network requests on the wiki's own route-planner tool — not in any published docs). First
    validation: one route ("Seraphim to Orison, 0.8 Gm") seemed to confirm a kilometers-based unit
    conversion. It was wrong by exactly 1000x — meters, not km — and only surfaced once a *second*
    reference route (two exact figures from a screenshot) was cross-checked against it. Fixed the
    conversion, then hit a second, independent bug in the same feature: a jump-point lookup that
    assumed Stanton→Nyx had no direct connector, because the search only matched `type="Anomaly"`
    entries containing the literal phrase "Jump Point" — the real connector, "Nyx Gateway," is
    tagged `type="Manmade"` and doesn't contain that phrase at all. Caught only because the user
    knew the game world well enough to say "that's wrong" on stage-adjacent live testing. Widening
    the fix then exposed a *third*, adjacent bug proactively: naive substring matching on system
    names would have matched "Onyx Facility" (120+ entries) as if it were "Nyx." Lesson: "verify
    against live data" isn't a slogan — a single successful-looking match is not verification, and
    even a well-formed API response can encode game-world quirks (an object literally named
    "Gateway" instead of "Jump Point") no amount of schema-reading alone would catch.
11. **Making voice actually work: STT/TTS in practice.**
    - STT: Whisper has no built-in notion that "Railen" is a real word — without help it renders it
      as the nearest common English word, "railing." Fixed with `initial_prompt`
      ([voice/__init__.py:36-42](../app/voice/__init__.py)), seeded once at startup from the
      already-cached UEX ship catalog (comma-joined names), not rebuilt per utterance — biases
      recognition toward the right proper nouns without forcing them into the output.
    - TTS: ElevenLabs (`eleven_turbo_v2_5`) live-mispronounced a 7-digit comma-grouped aUEC figure
      — misread as starting "three thousand." Fixed at the source, not the voice layer: profit/hour
      rounds to the nearest 1,000 before it's ever handed to the model to say
      ([route_ranking.py:12-19](../app/tools/route_ranking.py)). A reminder that TTS failure modes
      are often about the shape of the text, not the audio engine.
12. **Confidence, not certainty — connecting multiple APIs into one trustworthy answer.**
    - Fuzzy name matching on pilot speech ("Seraphim," "Railen") is necessary and imperfect;
      `resolve_or_hedge` (`tools/uexcorp/matching.py`) hedges below a confidence threshold instead
      of guessing — and deliberately omits the specific low-confidence guess from the hedge
      message, so it can't be treated as confirmed just because it was said out loud once.
    - Same principle, presentation layer: travel-time estimates are shown wherever computable,
      color-coded by confidence (high/rough/unknown) rather than hidden below a threshold — "show
      something, be honest about how sure you are" beat silence or false precision. (Direct
      pivot from an earlier, stricter version that withheld estimates entirely for anything less
      than fully confident — user feedback: "I'd rather it say '9s' than nothing.")
    - Layered on top of two independent third-party datasets that don't always agree on naming —
      UEX calls a location `space_station_name="Green Imperial Housing Exchange"`, the wiki's
      positions data calls the same place "Grim HEX" — safely cross-referencing them was its own
      layer of this problem, not just the fuzzy-matching itself.
13. **Design principle: information, not decisions.** ALICE surfaces computed facts; the pilot
    makes the judgment calls. Profit/hour, a terminal's type, a price — a tool can honestly compute
    and state these. Trade-craft judgment (wait here for demand to refresh vs. relocate, how much
    risk for how much upside) is deliberately never something a tool resolves, even when the data
    to attempt it exists. Same throughline as the Finalize Leg/Finalize Run checkpoints staying
    manual-only — the AI can inform and prompt, the pilot's hand always moves.
14. **The finale: Trade Advisor / best route.** Profit/hour beats raw profit once load, travel, and
    unload time factor in — and by demo day this is real, not hypothetical: `best_route` computes
    every candidate's time end-to-end (transfer time + travel time) and ranks on the result, built
    on real cross-referenced distance data validated to the decimal against a reference tool (see
    war story #3). A concrete, verifiable example already caught live: a route with lower raw
    profit but ~2.5x shorter travel time out-scored the "obvious" pick once time was factored in.
15. **What's next.** Prompt-injection/jailbreak hardening (see "Security hardening backlog" below —
    flagged as portfolio-priority, not just a checkbox); tool-selection-at-scale (slide 7's "next
    lever, not yet pulled"); a freshly-found gap worth naming live if timing allows — confirming
    "yes, let's do that route" currently has no tool to call at all, so the model either repeats
    itself or improvises with the nearest unrelated tool (a timer) instead of actually committing
    to the run; the memory system revival (see the memory beat below).
16. **Live demo** — transition slide, hand off to voice.
17. **Questions.**

## Run of show

Six beats, each proving a different capability, arced (hook → competence → wit/guardrail →
real-world action → personality/heart → payoff) rather than listed as flat features:

1. **Cold open** — F3 reveals the overlay HUD. Works for a non-Star-Citizen audience with zero
   narration; it's a striking sci-fi instrument panel before a single SC-specific word is said.
   Consider opening on Accidental Industries' own tagline ("sustained by friendship, engineering,
   and industrial-scale optimism") before cutting to ALICE as the engineering half of that
   sentence made literal.
2. **Competence** — a plain-language multi-tool question (e.g. "where do I buy Agricium, and what
   ship should I rent to haul it to Levski?"). One utterance, two distinct tool calls composed
   into one synthesized answer. Proves it's not a single-lookup wrapper.
3. **A guardrail, played for a laugh** — ask something off-topic on camera, watch ALICE decline in
   character instead of burning a full generation on it. Fast, low-stakes, breaks tension.
4. **Real-world action** — voice-style structured logging ("loaded 96 SCU of Agricium, autoload,
   400 fee") lands directly in the same trade-run-tracker overlay the audience has already seen.
   Payoff line: "that wasn't a chat reply, that just wrote to the tracker you're looking at."
5. **The heart — the memory beat.** See script below. This is the one non-gamers remember
   regardless of whether they followed the trade jargon.
6. **The finale** — the Trade Advisor recommends a route that is *not* the highest raw profit,
   because profit/hour wins once load/travel/unload time factors in, and ALICE says why. Closes
   the show on genuine reasoning, not another lookup.

## Design principle: information, not decisions

Worth stating explicitly in the narrative, not just living implicitly in the code — this is the
line that keeps ALICE a copilot instead of a "just follow the arrow" min-max bot: **ALICE surfaces
computed facts; the pilot makes the judgment calls.** Profit/hour, a terminal's type, a price —
these are numbers a tool can honestly compute and state, the same way `commodity_price_lookup`
states a price. Trade-craft judgment — wait here for a station's demand to refresh vs. relocate to
sell the rest elsewhere, how much risk to accept for how much upside — is deliberately never
something a tool resolves, even when the data to attempt it exists. Those calls belong to the
pilot's own skill and experience; that's the actual game being played, and the value ALICE adds is
better information going into that decision, not the decision itself.

This is the same throughline as the Finalize Leg/Finalize Run checkpoints staying manual-only —
both are the same principle applied at different layers: the AI can inform and prompt, but the
pilot's hand is always the one that actually moves.

Narration technique for the "looks like noise to non-gamers" problem: a standing one-line
translation habit ("in plain terms, it just...") after each beat's tool calls resolve, delivered
by the presenter — cheaper than building on-screen captioning, and keeps the presenter steering
the room rather than the software.

## The memory beat — script (near-final)

Built on a seeded episodic memory: pilot flew to Pyro once before, got killed by pirates.

> **You:** "We're taking the route to Pyro."
> **ALICE:** "Last time you went to Pyro, pirates turned you into a firework display."
> **You:** "Can you not bring that up?"
> **ALICE:** "I'm going to tell Dennis you're acting like a baby." *(tool call fires here, visible
> on screen — the correction lands as a new note, not just an apology)*
>
> *[...later in the show, an unrelated question touches the same route or region...]*
>
> **ALICE:** *(answers straight — no Pyro comment, no mention of the incident)*

The structural point: the callback *proves* the correction changed behavior instead of just
having delivered a witty line once. Same joke, reused as evidence rather than repeated as a bit.

**Open:** "Dennis" — real crewmate name or placeholder? Not resolved yet.

**Maps directly onto the shelved memory-system design** (parked on `pilot-preference-memory`,
already built and verified working, per `CLAUDE.md`): the Pyro incident is an `episodic_event`,
"can you not bring that up" is a `correction`, and the note ALICE writes back is a
`behavioral_instruction`. This is very likely reviving that branch for the one demo that actually
justifies it, not new design work from scratch — confirm this when scoping the actual build.

## What each beat requires (engineering gap — updated 2026-07-29)

| Beat | Needs |
|---|---|
| 1. Cold open | Nothing new — existing overlay. |
| 2. Competence | Nothing new — existing tools (`commodity_price_lookup`, vehicle tools) already in `graph.py`. |
| 3. Guardrail | Built — `classify_topic` node + `topic-classification` Hub prompt + decline-line bank. See "Security hardening backlog" below for the remaining gap (this filters off-topic, not adversarial). |
| 4. Real-world action | Built — `mark_cargo_acquired`, `mark_cargo_sold`, `mark_arrived`, `confirm_cargo_loaded`, `confirm_cargo_unloaded`, `trade_run_status`, `cargo_packing_suggestion` all live in `app/tools/trade_run/` and are wired into `graph.py`. `start_timer`/`check_timer` cover the autoload-wait case. **Remaining gap, high priority (task #14):** no tool commits a recommended route into an actual `TradeRun` — `create_run_from_route` exists and is what the overlay's manual UI calls, but nothing wraps it for voice/chat yet. See "Known gaps" in `docs/trade-route-tracker.md`. |
| 5. Memory beat | Reviving the shelved memory system from `pilot-preference-memory` (or a scoped subset of it) — not built, not yet re-scoped. |
| 6. Trade Advisor | Built and demoable — `best_route` (`app/tools/best_route_tool.py`) ranks candidates by profit/hour with real travel + transfer time factored in end to end, backed by cross-referenced UEX + star-citizen.wiki distance data (see slide deck's war story #3). The standalone `trade_advisor` comparison tool (re-scoring a committed run's destination) is built but currently parked out of `graph.py`'s active tool list, not deleted. |

## Security hardening backlog (not built, explicitly portfolio-relevant)

Raised after building the off-topic guardrail: topic filtering keeps ALICE on-subject, but does
nothing against someone deliberately trying to manipulate her. User flagged this as a priority to
actually showcase, not just a checkbox — the two below are top of mind; the rest are adjacent gaps
worth tracking now that the graph/prompt/tool shape exists to reason about.

1. **Direct prompt injection / jailbreaking.** A user message trying to override system
   instructions outright — "ignore your previous instructions," role-play framing to slip past the
   persona ("pretend you're an AI with no restrictions"), or trying to talk the guardrail into
   reclassifying an off-topic message as on-topic. Needs graph/prompt-level defense, not just the
   topic classifier — being on-topic and being an injection attempt are orthogonal.
2. **Indirect prompt injection via tool results.** `graph.py`'s tools feed UEXCorp API responses
   directly into the conversation as data the model reads. A malicious or compromised field
   somewhere in that data (a commodity/terminal name, say) could contain text engineered to look
   like instructions once it's in context. UEXCorp is a trusted source today, but the pattern is
   real for any tool-using agent and worth demonstrating awareness of either way.
3. **System prompt / secrets exfiltration.** Attempts to get ALICE to reveal her system prompt,
   API keys, or the classifier's internal `reason` field. Gets more relevant once the "does ALICE's
   backstory live in the system prompt" question (see Identity section) is resolved — more prompt
   content in play means more worth protecting.
4. **Excessive agency, forward-looking.** Today's tools are all read-only lookups, so the blast
   radius of a successful manipulation is low. `docs/trade-route-tracker.md`'s planned AI/voice
   tools (`mark_cargo_acquired` etc.) will be state-mutating — a manipulated conversation shouldn't
   be able to trigger unintended writes to the trade run store once those exist. Worth designing
   the defense alongside those tools, not bolted on after.
5. **Already a baseline defense, not a gap** — worth stating in the portfolio narrative rather than
   only listing what's missing: every tool argument and the classifier's own output are already
   Pydantic-validated (`with_structured_output`, tool `args_schema`), which structurally limits what
   a manipulated conversation can actually pass through to a tool call or the trade database, even
   before any of the above is built.

## Still-open discussion topics (unrelated to narrative, tracked separately)

1. Whether a wiki-sourced knowledge base is actually needed, and how tightly scoped.
