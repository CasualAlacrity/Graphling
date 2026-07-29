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
**Suggested slide template for all three war stories below** — one consistent layout makes them
fast to build and easy for the audience to pattern-match across: **Trace → Wrong First Guess (where
there was one) → Root Cause → Fix → Lesson**, one per slide or split across two if the trace needs
room to breathe. The trace quotes are real, pulled from actual LangSmith runs / recorded testing
sessions, not reconstructed after the fact — worth showing verbatim on screen (a monospace block)
rather than paraphrasing, since "here's the literal broken output" is more convincing to a technical
audience than a description of it.

8. **War story #1 — a good tool description still didn't fire.**
   - **Setup:** a real trade run active (Seraphim → Pyro, hauling RMC, in a Railen). Pilot reports
     arrival in plain speech, the way `mark_arrived` is designed to be triggered.
   - **Trace:**
     > **Pilot:** "Just docking in the Seraphim station now."
     > **ALICE:** *(pure conversational reply — no tool call, `tool_calls: []`)*

     Same result on a second, independent phrasing in an earlier test: "I've just landed at
     Orison" also produced `tool_calls: []`.
   - **The near-miss that makes this one sting:** `mark_arrived`'s own tool description reads
     *"call this when they report arriving somewhere in transit — 'I'm here', 'just landed at
     Area18'"* — almost the pilot's exact words. Verified via the LangSmith trace, not a guess,
     that the tool schema itself was clean (`convert_to_openai_tool` showed a well-formed schema) —
     ruling out an args/schema bug before looking anywhere else.
   - **Wrong first guess:** first instinct was that the persona prompt needed an explicit,
     per-tool list of trigger phrases, duplicating each tool's own description. The user pushed
     back — *"Shouldn't it know from the tool names and descriptions in the code? It seems
     incredibly error prone if I need to always manually add them"* — and that was the right
     objection: `bind_tools()` already sends every tool's name and description to the model on
     **every single turn**, automatically. Duplicating it in the persona would've been a second
     place to keep in sync, exactly the kind of drift this project has been allergic to all along.
   - **Root cause:** the persona prompt's overall framing never established that *acting* on the
     pilot's behalf was in scope at all — every section (Identity, Instruction Priority, Answer
     Discipline) frames ALICE purely as a question-answering assistant over trade data. A smaller
     model (`gpt-4o-mini`) leans more on explicit role-framing than a frontier model would; without
     ever being told that acting is even part of the job, it defaults to treating everything as
     something to respond to, no matter how well any individual tool is described.
   - **Fix** — one generic paragraph in the persona, not a per-tool list:
     > "You also have tools that update the pilot's live trade run (arrival, purchases, sales,
     > loading/unloading confirmations). When the pilot reports one of these has actually
     > happened, call the matching tool — do not just acknowledge it conversationally. Trust each
     > tool's own description for exactly when it applies; do not wait for a specific phrasing."
   - **Lesson:** tool selection isn't just a function of the tool's own description — it's gated
     by whether the system prompt's self-concept for the agent includes that class of action at
     all. (Worth keeping the wrong first guess in the story, not editing it out — it's the more
     instructive version, and it's the audience's own likely first instinct too.)
9. **War story #2 — the hallucinated destination.**
   - **Setup:** mid-run, pilot sends a compound message — a milestone report and a question,
     stacked in one utterance.
   - **Trace:**
     > **Pilot:** "Cargo's loaded. Heading out. What's my destination?"
     > **ALICE:** *(answers the destination question fluently and confidently — and wrong. No tool
     > call for the milestone either: `tool_calls: []`.)*

     Fabricated answer: **"Admin at GrimHEX, in orbit around Yela."** The real destination on that
     run was Seer's Canyon — a different terminal, confirmed later against the actual database row
     once `trade_run_status` existed to check it.
   - **The tell — this wasn't a generic guess.** Traced the fabricated sentence back to its exact
     source: the persona prompt's own worked example for how to phrase a commodity price —
     *"Admin at GrimHEX, in orbit around Yela, is buying Iron for 2,700 aUEC per SCU"* — a
     formatting sample about Iron pricing that has nothing to do with destinations at all. With no
     tool able to answer "what's my destination," the model pattern-matched to the nearest
     fluent-sounding, correctly-formatted sentence sitting in its own context window and repurposed
     it wholesale.
   - **Compounding failure, same trace:** the milestone half of the message ("Cargo's loaded") also
     never fired `confirm_cargo_loaded` — with a question sitting right there, the model answered
     it and silently dropped the action. Same *shape* of bug as war story #1 (acting lost out to
     answering), different trigger — one utterance, two independent failures.
   - **Fix, two-part:**
     1. Built `trade_run_status` so there was a real source of truth to call — reused existing
        `current_leg`/`next_unset_field` functions already in `trade_run_store.py`, verified live
        against a real run to confirm it reported the correct sale-leg terminal, not just the
        current one.
     2. Tightened the persona: "never assume or invent a trade run's current state," plus an
        explicit note that its own worked examples "describe format and phrasing only, never real
        data" — closing off the leak vector directly, not just patching the symptom.
   - **Lesson:** hallucination isn't always fabrication from nothing — sometimes the model is
     retrieving from the *wrong place* in its own context, and a system prompt's own few-shot
     examples can be the leak vector if nothing else is available to answer from.
10. **War story #3 — trust one data point, get burned twice.**
    - **Setup:** built travel-time estimation on an undocumented API
      (`star-citizen.wiki`'s `locations/positions` endpoint) — found by watching network requests
      on the wiki's own route-planner tool, not from any published documentation.
    - **Bug #1 — the unit conversion.** First validation used a single route: "Seraphim to Orison,
      0.8 Gm," which seemed to confirm a kilometers-based conversion (`/1,000,000`). It was wrong
      by exactly **1000x** — the real unit is meters (`/1,000,000,000`) — and the error only
      surfaced once a *second*, independent reference route (two exact figures pulled from a user
      screenshot) was cross-checked against it. The first "successful" match was a coincidence, not
      confirmation.
      > **Lesson so far:** one matching data point isn't verification — it's an unfalsified guess.
    - **Bug #2 — the missing jump point.** After fixing the units, a jump-point lookup for
      Stanton→Nyx returned nothing, because the search only matched entries tagged
      `type="Anomaly"` that literally contained the phrase "Jump Point." The real connector, **"Nyx
      Gateway,"** is tagged `type="Manmade"` and doesn't contain that phrase at all — so the
      honest-sounding conclusion "no direct connector exists" was itself wrong. Caught only because
      the user knows the game world well enough to say, flatly, "Stanton absolutely has a Nyx Jump
      point" — a domain-knowledge correction no amount of re-reading the API response would have
      produced alone.
    - **Bug #3 — found proactively while fixing #2.** Widening the type filter to catch
      "Gateway"-style connectors meant searching system names as substrings — which would have
      silently matched "**Onyx** Facility" (120+ unrelated entries) as if it were "Nyx." Caught and
      fixed (word-boundary matching) before it ever shipped, by noticing the risk while touching
      the code, not from a bug report.
    - **Lesson:** "verify against live data" isn't a slogan — a single successful-looking match is
      not verification, and even a well-formed API response can encode game-world quirks (an object
      literally *named* "Gateway" instead of "Jump Point") that no amount of schema-reading alone
      would catch. Three independent bugs in one feature, each caught a different way — a second
      data point, a domain expert's correction, and proactive suspicion while editing — which is
      itself the more useful lesson than any single fix.
11. **Making voice actually work: STT/TTS in practice.** Three linked fixes, landed together
    once the real failure was traced (`3c5ff88`) — the takeaway is that "the mic didn't hear it
    right" turned out to be three separate problems stacked on top of each other, not one:
    - **Problem 1 — Whisper mis-hears domain vocabulary.** Whisper has no built-in notion that
      "Railen" is a real word; without help it renders an unusual proper noun as the nearest
      common English word instead — a live trace showed "Railen" transcribed as "railing." Fixed
      with `initial_prompt` ([voice/__init__.py:36-42](../app/voice/__init__.py)): the full UEX
      ship-name catalog, comma-joined, built once from the already-cached data at voice-loop
      startup — not rebuilt per utterance — and passed straight into Whisper's decoder. Biases
      recognition toward the right proper nouns without forcing them into the output.
    - **Problem 2 — the hint helps, but doesn't guarantee a fix, so the fuzzy matcher needs its
      own safety net.** Even with the vocabulary hint, a near-miss transcription can still slip
      through — so ship-name matching (`travel_time`/`trade_advisor`) was changed from a plain
      "take the best match" (`match_by_name_or_code`) to a confidence-scored variant
      (`match_by_name_or_code_with_score`, `tools/uexcorp/matching.py`) that can tell a
      barely-passing match from a real one. Concretely: a mis-transcribed "railing" matched
      "Railen" at a score of exactly 60 — one point above the 60-point cutoff, zero margin. Below
      `LOW_CONFIDENCE_MAX = 80`, the tool now hedges out loud ("not sure which ship you meant —
      closest match is the Railen, confirm?") instead of silently committing to a guess that
      happened to clear the bar by luck. This is the same `resolve_or_hedge` pattern generalized
      later in the session (slide 12) — first built here, for this exact failure.
    - **Problem 3 — a real bug hiding behind the other two.** Fixing the vocabulary hint and the
      confidence gate exposed a third, unrelated bug underneath: even a *correct* fuzzy match
      (e.g. "Railen" matched confidently) was still failing to fetch ship-speed data, because the
      code queried the wiki API with the pilot's raw, misspelled input string instead of the
      matched item's canonical name. Fixed by resolving to `vehicle.name` first, then querying
      speed data with that — a reminder that a passing fuzzy match is only half the pipeline; every
      downstream lookup needs the *resolved* name, not the original utterance.
    - **TTS:** ElevenLabs (`eleven_turbo_v2_5`) live-mispronounced a real profit/hour figure —
      "3,547,877 aUEC/hour" was read starting with "three thousand," not the real value; a 7-digit
      comma-grouped number apparently trips up the model's number normalization. Fixed at the
      source, not the voice layer, and for two reasons at once: profit/hour is already an
      extrapolation (this exact trade repeated continuously for an hour), so reporting it to the
      exact aUEC was false precision regardless of TTS. `profit_per_hour()`
      ([route_ranking.py:12-19](../app/tools/route_ranking.py)) rounds to the nearest 1,000 before
      the figure is ever handed to the model to say, applied everywhere `best_route`/`trade_advisor`
      report a rate (`d6eadc9`). Lesson: the TTS failure mode here wasn't audio quality — it was
      the shape of the text being spoken.
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
