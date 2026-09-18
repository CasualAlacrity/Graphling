# Ledger trust, corrections & recoverability — design notes

**Status:** design intent captured 2026-09-10, nothing built. Sources, both held outside
this repo (see "Where documents live" in `docs/intent/README.md`): an HCI-course working
session transcript, and *ALICE — design rationale and HCI specifications*. Everything
load-bearing from them is restated here rather than referenced. Flagged at the time: "I
don't think we have the ability to do that yet — we should make sure we accommodate
these."

Grepped 2026-09-10: no `source` / `confidence` / `provenance` / `estimate` / `override` /
correction concept exists anywhere in `app/db/` or `app/tools/trade_run/`. All net-new.

## 1. Provenance + confidence on recorded values

Provenance belongs on the **event/value**, not just the run.

- Every recorded value (`price_per_unit`, `quantity_scu`, `cargo_transfer_fee`, arguably
  the milestone timestamps) carries `source` and `confidence`.
- Sources:
  - `api` — a real quoted number (e.g. a UEX price at record time)
  - `planned` / `expected` — the value from the committed UEX route, not yet confirmed
  - `user` — the pilot stated it (trusted but unverifiable)
  - `forced` — the pilot force-completed a step and ALICE filled expected values (low, §2)
- The run keeps an **aggregate** reliability figure, but it must drill down: "why is this
  run lower confidence?" → name the specific event.
- Mental-model target (from the transcript, verbatim intent): **not** "ALICE says this run
  is 82% trustworthy" — instead "most of this is verified; this purchase amount was
  estimated because I forced the step."

**Schema shape — decide before Phase 2's migration lands** (that migration is already
adding `user_id`; fold this in rather than a second pass):
- Option A: `*_source` / `*_confidence` columns paired with each value on `TradeLeg`.
- Option B: a small `LegValueProvenance` / event-audit table (`leg_id`, `field`, `value`,
  `source`, `confidence`, `recorded_at`). More flexible, supports correction history (see
  §3), heavier.
- Leaning B — corrections and force-complete both want a history, not just a current flag.

## 2. Force-complete / graceful degradation

When a milestone is blocked on data the pilot can't supply, offer to proceed with
expected values instead of collapsing the workflow.

- **Soft constraint, not hard.** Not "state invalid, refused" — instead "I can't normally
  make that transition because something's missing. Let's figure out whether it happened,
  collect what I need, or deliberately override."
- Force-complete uses `planned`/`expected` values (committed route price, planned qty),
  writes them at low confidence, and unblocks the leg.
- This is **recoverability** — the pilot escapes an abnormal state without abandoning the
  run — and **graceful degradation** — the run continues with reduced confidence.
- **Surfaced in the mini-modal:** outstanding data on the current leg renders as a `❗`
  line (`❗ Autoload fees: ???`) in the presence layer's reaction beat
  (`docs/presence-layer.md`). ALICE follows up on it verbally; a force-complete fills it
  and the line re-renders as `ℹ️ … (estimated)` at low confidence.
- Shape: either a `use_estimates: bool` path on the existing `record_purchase` /
  `record_sale` flow, or a distinct `force_advance` tool ALICE offers only after a normal
  call comes back "blocked, missing data". `_apply_transaction` currently hard-errors if
  `transaction_completed_at` is set — that guard needs a sanctioned bypass.

## 3. Post-hoc corrections

"ALICE, I actually paid 17.6 per unit" — amend a recorded value by voice.

- Identify leg + field, update the value, **re-derive dependents** (`run_profit`,
  profit/hour), tag the new value `source = user`.
- **Pre-finalize only.** Finalize locks fields for pilot sign-off
  ([[project-trade-run-finalize-checkpoints]]) — so a correction tool operates on
  non-finalized legs; post-finalize correction is manual / reopen-only, out of scope for
  voice.
- HCI note: if pilots routinely reach for "open the manual editor and fix the ledger"
  when "just tell ALICE" would do, the simpler affordance isn't being surfaced.
- New tool, e.g. `correct_trade_value(leg, field, value)`. No amend path exists today.

## 4. Missing prerequisite, not contradiction

ALICE has **no independent source of world-truth** in Star Citizen — no telemetry for
position, distance, or ship state (the "nav says we're 30 km out" scenario from the HCI
session was a hypothetical; that data doesn't exist, and isn't reliably OCR-able either).
The pilot is ALICE's only source of what's happening in the world. So she never
*contradicts* the pilot — the only two cases are:

- **Missing prerequisite** (the transition needs a step that hasn't happened): soft
  constraint — ask, never accuse. "Did you acquire the cargo? I don't have a record of
  that." Distinguish "I have no record of X" from "X didn't happen"; the system's
  incomplete representation of reality is not reality. This is §2's soft-constraint /
  force-complete path. Jeff's current design already leans this way; make it an explicit
  persona/relay rule.
- **Value already recorded** (the pilot states something different from what's stored):
  that's a correction (§3), not a conflict to push back on — update it, tag `source =
  user`.

No "surface the contradiction / override" flow is needed — there's nothing for ALICE to
contradict the pilot *with*.

## 5. Constrain the state machine, not the user's language

The LLM/NLP layer accepts unlimited phrasings ("landed", "touchdown", "we're here",
"landed and unloaded"); the workflow enforces strict transitions and never gains state
freedom. Already how the tools work implicitly — naming it so future tools keep the split.

## Related, smaller

- **Multi-milestone from one utterance** ("landed at Orison and unloaded the cargo" → two
  transitions). Believed working via `bind_tools` compound calls; needs an explicit test
  that milestones (not just tool calls) advance — a Phase 3 harness case.

## Open, not yet designed

- **Bare-answer recommendations.** The HCI session raised "where should I sell?" → "Area18"
  and nothing else as a weak interaction (the pilot has no basis to evaluate it), but Jeff
  hasn't worked a solution yet. `best_route`'s runner-up already gives the *why* a place to
  live; the open question is what a voice reply should surface by default vs. on request.
  Revisit later.
- **Formal HCI lenses** — mixed initiative, trust calibration, automation bias, appropriate
  reliance, explainability, the two gulfs, Norman's seven-stage cycle. Evaluation
  frameworks for design reviews. Apply later, not now.
