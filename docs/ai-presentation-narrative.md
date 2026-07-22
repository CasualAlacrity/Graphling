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

## What each beat requires (engineering gap, not yet built)

| Beat | Needs |
|---|---|
| 1. Cold open | Nothing new — existing overlay. |
| 2. Competence | Nothing new — existing tools (`commodity_price_lookup`, vehicle tools) already in `graph.py`. |
| 3. Guardrail | Built — `classify_topic` node + `topic-classification` Hub prompt + decline-line bank. See "Security hardening backlog" below for the remaining gap (this filters off-topic, not adversarial). |
| 4. Real-world action | AI/voice tools for the trade run store (`mark_cargo_acquired` etc., per `docs/trade-route-tracker.md`'s Build plan step 3 — not built). Natural extension: an autoload timer — "it'll take 8 minutes" starts a timer, proactively notifies when done, and supports a "how much longer" query in the meantime. Tracked in detail in `prompts/Example Prompts.md`'s Timer/Notification section — proactive notify needs voice as the primary path (Chainlit has no clean way to push a message from a background task), the "how much longer" query is ordinary request/response and has no such gap. |
| 5. Memory beat | Reviving the shelved memory system from `pilot-preference-memory` (or a scoped subset of it) — not built, not yet re-scoped. |
| 6. Trade Advisor | The scoring engine designed in `docs/trade-route-tracker.md` ("Trade Advisor — scoring & inferred preferences") — designed, not built. |

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
