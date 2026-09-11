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

`~/Documents/ALICE-design-rationale-and-HCI-specifications.docx` — an HCI-course writeup
that specifies the mini-modal interaction more tightly than the tiers below originally
did. Folded in:

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
- the display-intent event bus (tool → queue → Qt consumer)
- the **mini tracker** widget: current leg + next step; on advance, the done step gets
  struck through and fades, next step animates in ("Fly to Orison" → "Unload cargo")
- wire the `mark_*` trade-run tools to push `tracker_advance`

*After Phase 1 (finish trade tracker) — it renders run state.*

### Tier 3 — Transient cards
Reuse Tier 2's bus.
- timer countdown card ("show me the timer", or auto on `start_timer`)
- route suggestion: workbench **open** → highlight + pin to top of the results panel
  (already has a `set_routes` slot); workbench **closed** → the non-modal card

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
