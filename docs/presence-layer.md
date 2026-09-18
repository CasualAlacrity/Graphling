# ALICE presence layer — design & plan

**Status:** vision captured 2026-09-09, not started. A UI track that runs alongside the
main roadmap (`docs/todo.md`), not part of it.

## The idea

ALICE's voice responses should also produce something **visible**, so she feels like
she's making things happen rather than just talking into your ear. Mini tracker, on-screen
timer, route-suggestion card, op invites — all the same idea, one architectural layer with
many instances.

## Two surfaces, kept separate

| | The workbench (exists) | The presence layer (new) |
|---|---|---|
| What | F3 toggle, large, filter / results / trade-runs / ledger tabs | lightweight, always-available, shows ALICE's current state |
| When | sitting down to plan | glanceable during actual gameplay |
| Lifecycle | toggle open/closed | mini tracker persistent-ish; cards animate in and auto-dismiss |
| Examples | route search, ledger review | "Fly to Orison" step, timer countdown, route card, op invite |

They share data (trade run store, resolver, uex_lookup) but are different windows with
different behaviour. Do not merge them.

## Mechanism: tools emit display intents

Voice today: `voice → graph → text → TTS`. Add a side channel — **tools push structured
"display intent" payloads** to a queue the Qt presence layer consumes:

- `best_route` runs → pushes a `route_suggestion` payload
- `start_timer` runs → pushes a `timer` payload
- `mark_arrived` / `mark_cargo_*` run → push a `tracker_advance` payload

Tools emit events; nothing parses ALICE's prose. Deterministic and testable — same
philosophy as the tool-selection harness. Each tool that should have a visual gets a small
addition (tool code — Jeff's side); rendering is all Qt (Claude's side).

**Process topology:** requires voice + overlay co-resident in one process. That is now the
*only* run mode — Chainlit is cut, "just ALICE" is the whole package (see
`project-chainlit-ui-ceiling` memory). The in-process bus can be a `queue.Queue` or a Qt
signal. Cross-client events (group ops) ride the shared Postgres later.

## Decisions made (2026-09-09)

- **Mini tracker click-through:** try *without* `Qt.WindowTransparentForInput` first, see
  how it feels. Small corner strip that never overlaps clickable game UI may be enough.
  This does reopen the question `trade-route-tracker.md` closed ("toggle-open, not a
  persistent HUD") — for the mini tracker specifically.
- **Closed-workbench route card is non-modal** — it just *feels* modal: animates in, has
  Accept / Dismiss, auto-dismisses on timeout or next command. A true focus-stealing modal
  mid-flight would be annoying.

## From the HCI design-rationale doc (2026-09-10)

Source: *ALICE — design rationale and HCI specifications*, an HCI-course writeup held
outside this repo (see "Where documents live" in `docs/intent/README.md`). It specifies
the mini-modal interaction more tightly than the tiers below originally did. Everything
load-bearing is folded in below, so the external doc is provenance, not a dependency:

- **The interaction is a three-beat loop, not just a step label:**
  1. *Before the pilot acts* — show the next step **and** a speaking tip tied to that
     specific step ("Fly to Orison" → say *"I landed at Orison"*). The tip is about
     reporting *this* accomplishment, not a generic push-to-talk intro. (Motivation:
     testers found bare push-to-talk intimidating.)
  2. *After the pilot reports* — a swift animation showing **what ALICE logged**, not
     merely "got it". "An indicator that something happened doesn't tell you what
     happened." Three line kinds:
     - `✔️` — a state transition (one utterance can produce two)
     - `ℹ️` — a data value captured, indented under its transition (`400 aUEC/unit`)
     - `❗` — a data value still needed, indented, shown as `???` (`Autoload fees: ???`)

     e.g. pilot says *"Sold the cargo, 400 per SCU"*:
     ```
     ✔️ Cargo unloaded
     ✔️ Cargo sold
          ℹ️ 400 aUEC/unit
          ❗ Autoload fees: ???
     ```
     The `❗` lines are what ALICE follows up on verbally, and what a force-complete would
     fill (then re-rendering as `ℹ️ Autoload fees: ~120 (estimated)` at low confidence —
     ties to `docs/ledger-trust-and-corrections.md`).
  3. *Then* — the `✔️`/`ℹ️`/`❗` lines **fade out after a few seconds**; they are a
     transient reaction, not a running log. The mini tracker's **resting state is
     minimal — just the next action** (`Next: Travel to Area18`). It never grows into an
     ever-building list of everything done this session.
  Beat 2 is a **Tier 2** requirement (the mini tracker), not a Tier 4 onboarding extra.
- **First-use onboarding wording teaches flexibility, not magic commands.** First run:
  "Tell ALICE when you've loaded the cargo. Say it naturally — for example: *Cargo
  loaded.*" Then rotate the example phrase across sessions so the pilot discovers the
  interaction is flexible. Showing one fixed phrase forever accidentally teaches "ALICE
  has a set of voice commands"; "say it naturally" + rotation corrects that.
- **Structured payloads, not prose.** For beat 2 to render cleanly, the `mark_*` /
  `confirm_*` display intents must carry the structured result — leg, milestone(s)
  stamped, **values captured this turn** (for the `ℹ️` lines), **values still outstanding
  on this leg** (for the `❗` lines), and the next step — not the text string those tools
  return today. `start_trade_run`'s payload likewise carries the new run's leg summary.
  This is a concrete field list for the "tools emit display intents" mechanism above, and
  a note for the tool designs.
- **Open questions resolved** (the doc left these unspecified):
  - *Must the pilot say the suggested phrase exactly?* **No.** Recognition is the normal
    NL tool path (`resolver`, `resolve_or_hedge`); the tip is a suggestion, any phrasing
    that fires the tool works. Anything stricter fights the rest of the system.
  - *Placement / dismissal / persistence* — covered by the corner-strip, non-modal,
    no-click-through decision above.
  - *Automatic arrival detection* — out. Consistent with "no Auto-Play" and
    finalize-stays-manual: the pilot reports, ALICE never infers a milestone.
- **Philosophy check.** The doc's core rule — ALICE presents information and options, the
  pilot makes the choices, no Auto-Play (see memory `project-information-not-decisions-`
  `principle`) — applies to this layer: the presence layer *shows* state and *suggests*,
  it never advances a run or commits a route on its own.

## Option sets, and the producer model (2026-09-18)

### The layer isn't only a tracker

ALICE offering two routes and the pilot answering "go with option 1" is the same
architecture as the mini tracker, but it changes what the layer is *for*: a general
surface for making feedback between pilot and ALICE unambiguous, not just a leg display.

This is not polish. `project-information-not-decisions-principle` — "ALICE presents
information and options; the pilot chooses" — is currently implemented **orally**, which
is the weakest available channel for comparison. Two routes with four figures each, read
aloud, to someone who is flying: nobody holds that well enough to genuinely choose. They
take whichever they heard last, or whichever sounded more confident. A visual comparison
is what turns the stated principle into a real one.

### No new plumbing — the data is already structured

The tools already produce `UEXTradeRoute` objects; the UI consumes the same objects the
tool emitted. Nothing parses prose, and there is no extra resolution step. An earlier
concern that the spoken ordering and the displayed ordering could disagree was unfounded:
display order and stash order both come from one tool call, so "option 1" resolves
deterministically regardless of how ALICE narrates it.

The token work from 2026-09-18 (`docs/start-route-tool.md`) already supplies the
selection half — every route named aloud now carries its own `route_token`, so picking the
alternative resolves the same way "let's do it" does.

### The producer set is tools *and* the loop

The mechanism above says "tools emit display intents." That's insufficient on its own:
display intents fire when a **tool runs**, but nothing has run yet between the pilot
finishing their utterance and the model deciding what to call. If inference is slow — a
saturated local model, a provider fallback — the pilot gets silence *and* a resting
screen, which is indistinguishable from ALICE never having heard them.

So the **voice loop is also a producer**: it pushes a `request_received` intent as soon as
`listen_once()` returns, cleared when the response plays. Still deterministic, still no
prose parsing — the producer set is just wider than tools alone. Cheap now, an awkward
retrofit once the bus has a tool-shaped API.

Related gap, same seam: the loop is strictly sequential (`listen_once` → `ainvoke` →
play), so nothing listens while ALICE is thinking or speaking. A pilot asking "what's
taking so long" isn't queued or ignored — they're never captured, and there's no barge-in
to interrupt a wrong answer. Same missing plumbing as `find_detour_pickup`'s
proactively-initiated turn, and `voice/timer_tool.py`'s `_notify_when_done` is the
existing hacky precedent (speaks from a background thread, bypassing graph and persona).
Three consumers, one seam — worth building once, deliberately.

### What an option card shows, and in what order

Two routes differ, in descending order of salience, by **source, destination, commodity,
and price-per-unit**. SCU is *not* a choice dimension — it falls out of stock levels and
hold size once a route is picked, and two routes will commonly reach the same load anyway
(a full Railen of Iron and a full Railen of Aluminium are both ordinary outcomes).

More SCU is not better. `find_best_route` ranks purely on profit per unit time; SCU enters
only as an input to profit. Which creates a UI constraint: **showing SCU at the same
visual weight as profit/hour implicitly tells the pilot it's a decision factor.** A bigger
number reads as better whether or not that was intended, so it would quietly argue against
the ranking. Profit/hour is the dominant element, with the delta between options legible —
that delta is also what makes the runner-up *answer* "why is this the best route" rather
than merely sitting there.

### Card layout mirrors the utterance

The spoken form ends on the total: *"96 SCU of Iron to Y — about N aUEC/hour."*
Itemization first, sum last. That ordering is correct and shouldn't be
"improved" by leading with the headline figure — it's the receipt/invoice pattern, and it
is *stronger* in speech than on screen. On a page the eye can jump to the total and back;
in audio the listener receives it strictly in order, and the last thing said is retained
best. The detail ahead of it is what gives the number meaning when it lands.

Therefore the card resolves to the profit figure in the same position the sentence does.
Matching the two channels makes them reinforce each other; diverging makes them compete
for a pilot who is hearing and seeing the same information at once.

### Keeping the layer bounded

The discipline that stops this becoming a UI framework is the one already stated —
structured payloads, fixed kinds, nothing parses prose. If every new communication need
invents a card type it sprawls; a small enumerated set (tracker, timer, option set,
confirmation) stays tractable. The `feedback-no-scrollbars-in-overlay` rule caps option
count naturally at two or three, which happily matches what a person can weigh while
flying.

## Tiers (with dependencies)

### Tier 1 — Polish what exists
Pure animation, no new surfaces. Builds `app/overlay/animations.py` (helpers:
`fade_in`, `pulse_highlight`, `count_up`, `slide_in`) that every later tier reuses.
- open/close transition (replace the hard `setVisible` in `overlay_app.py`)
- tab crossfade
- milestone advance shows movement/flow, not an instant jump
- route search results fade in instead of pop
- leg progress animation

*Can start anytime — independent of everything else.*

### Tier 2 — Presence layer foundation
- the display-intent event bus (tool *and voice loop* → queue → Qt consumer — see
  "The producer set is tools *and* the loop" above)
- `request_received` intent from the loop, so a slow turn doesn't look like a dropped one
- the **mini tracker** widget: current leg + next step; on advance, the done step gets
  struck through and fades, next step animates in ("Fly to Orison" → "Unload cargo")
- wire the `mark_*` trade-run tools to push `tracker_advance`

*After Phase 1 (finish trade tracker) — it renders run state.*

### Tier 3 — Transient cards
Reuse Tier 2's bus.
- timer countdown card ("show me the timer", or auto on `start_timer`)
- route suggestion: workbench **open** → highlight + pin to top of the results panel
  (already has a `set_routes` slot); workbench **closed** → the non-modal card
- **option set** — two or three routes side by side, answered with "go with option 1".
  Selection already resolves via the per-route tokens (`docs/start-route-tool.md`); layout
  and field priority are specified above

*After Tier 2.*

### Tier 4 — Onboarding
- mini tracker step text carries a voice hint: `Fly to Orison` + `(say "I landed at Orison")`
- verbose first-run mode that dials back as the pilot demonstrates each command
- mostly content + a small state machine

*After Tier 3.*

### Tier 5 — Group ops
- op invite card: "Jeff invited you to join the op", terms + reward-distribution breakdown
- accept → ALICE adds you to the op
- reward calc + per-person distribution when the op closes

*Needs multi-tenancy (roadmap Phase 2) + Tier 2-3 + cross-client events. Genuinely later —
matches "group runs come later, individual co-pilot first".*

## Why this matters beyond polish

- **Embodiment** — makes ALICE feel like she's doing things, not just narrating.
- **Onboarding** — the visible step + "say this" hint teaches voice navigation with no
  separate tutorial.
- **Group ops framework** — the invite/terms/accept/settle flow is a presentation-layer
  pattern, not a new subsystem, once the layer exists.
