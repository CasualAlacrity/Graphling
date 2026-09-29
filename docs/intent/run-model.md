# Intent — trade-run model: corrections, remainders, and sell-only runs

**Status:** not started (2026-09-29). The first executable slice — editing recorded leg
values — is specced in `docs/ledger-edit-path.md`. Everything else here is still design.
Tracked in `docs/todo.md` Phase 1 ("URGENT — recorded leg values can't be edited").

## Problem

The ledger assumes every run goes exactly as planned: one purchase, one sale, same
quantity, typed or spoken correctly the first time. Real play breaks all of that.

- **Recorded values are permanent** (checked 2026-09-29). Once `record_purchase`/
  `record_sale` runs — overlay *or* voice — the server refuses a second record and has no
  update endpoint; Mark Done and Finalize Run show read-only recaps (`build_recap_grid`);
  Abandon disappears once anything is bought (`_can_abandon`). The overlay Buy/Sell form
  before submit is the only edit window, and the voice path skips it entirely. One
  Whisper mishearing (614 for 640) is a permanent wrong ledger entry.
- **The Finalize step reviews nothing it can fix.** It exists so the pilot checks the run
  before locking it — but a check with no way to correct what it finds is a rubber stamp.
- **Quantities don't connect across legs.** Buying 614 against a 640 plan leaves the sale
  leg planned at 640 (`_apply_transaction` only touches its own leg), so the Sell form and
  voice "sold it all" both default to 640. Nothing stops selling *more* than was bought —
  the Sell quantity box allows 100,000 and the server never compares legs.
- **A partial sale has no follow-through.** The Sell form already says "Confirm partial
  sale", but then the run has unaccounted cargo and no way to sell it.
- **Lost cargo can't be closed.** Death, piracy, a crash — common in Star Citizen — leave a
  run with bought cargo that can be neither sold nor abandoned.
- **Only traders fit.** Miners, salvagers and pirates have cargo that never came from a
  terminal. The model has no run without a buy leg.

## Desired outcome

- A pilot can correct any recorded value, by hand or by voice, right up until the run is
  finalized — including right after ALICE reads it back ("640 at 14.2, logged." → "No,
  614.").
- A run's quantities always add up before it can be finalized, and every real-world
  reason they might not has a path.
- Miners and salvagers (Jeff's own other two activities) and pirates can track sales as
  runs, with honest profit.

## Decided

- **Run Finalize stays the lock point and stays manual-only** — unchanged from the
  existing finalize-checkpoints decision. Nothing about corrections makes Finalize
  AI-automatable.
- **Recorded values are editable until the run is finalized** — from the leg's Mark Done
  review and from the Finalize Run step, both legs (Jeff, 2026-09-29).
- **Editing a done leg keeps it done** — only its values change (Jeff, 2026-09-29). If done
  legs couldn't be edited, the Finalize step would be valueless.
- **A bought quantity carries forward to the sale leg** (Jeff, 2026-09-29). Correcting or
  recording the buy as 614 means the sale is planned at 614 — you can only sell what you
  bought.
- **Selling more than was bought is rejected.** It inflates profit and can't be true.
- **Why a remainder exists — four cases** (Jeff's 1–3, 2026-09-29; Claude added 4):
  1. *Wrong buy amount, no real cargo left* → a ledger edit before Finalize. This is what
     the manual Finalize step is for.
  2. *Waiting to sell here later* — most likely the terminal's demand filled mid-sale.
     Wait-vs-move-on stays the pilot's call (`trade_advisor` already refuses to weigh in).
  3. *Selling the rest somewhere else* — probably asking ALICE where.
  4. *Cargo is gone* (death, piracy, crash).
- **A run is one cargo source + N sale legs.** Remainder = acquired − Σ sold. The run stays
  in progress while remainder > 0, with the remainder visible; **Finalize Run requires
  remainder = 0.** Cases 2 and 3 are both "add another sale leg" (same terminal, or a new
  one). Case 4 is a **write-off** with a reason — kept for real loss only, not as the
  general way to close a remainder. Replaces "only ever one sibling per run"
  (`advance_leg`'s sibling-start logic, overlay rendering, and profit math all assume it).
- **Sell-only runs are the same model with a different source** (Jeff's idea,
  2026-09-29), not a separate trade type. Source is a buy leg (cost basis = price × SCU)
  or mined/salvaged/looted (cost basis 0). Profit = Σ sales − cost basis.
- **A trade run's leftover cargo stays on its own run.** Moving a remainder into a fresh
  sell-only run was rejected: it books a loss on the original run and a fake 100% margin
  on the new one. A carry-over (remainder moves to a later run *with* its cost basis) is
  possible later if "sell it next session" turns out to be common — not needed now.
- **Work split** (per `feedback-ask-before-editing`): server, schema/migrations, and
  overlay are Claude's to build; the AI tools (voice correction, "add a sale leg there",
  sell-only `start_trade_run` path) and their schemas are Jeff's.

## Constraints

- **No scrollbars in the overlay** — a hard rule. Edit forms, extra sale legs, and
  remainder rows have to fit by curating/collapsing, not scrolling.
- **Ownership is scoped transitively** (`ledger_service.py`'s header comment): id-based
  calls don't re-check the user. Any new id-based endpoint follows the same rule, and the
  same "revisit if ids start being shared cross-tenant" caveat applies.
- **Pilot-entered data never enters the shared pool** (`utterance-coverage.md`, "Deliberately
  not closing"). Corrections make the personal ledger more accurate; they don't make it
  shareable.
- Profit/hour ranking and ledger history both read these tables — a model change must keep
  `trade_run_store.py`'s pure helpers (`run_profit` and friends) correct for N sale legs
  and a zero-cost source.

## Open questions

- **Write-off voice path.** "I got pirated, write it off" is natural mid-flight, but
  write-off is close to Abandon, which is decided overlay-only (2026-09-24). Leaning
  overlay-only for consistency; Jeff's call.
- **Editing transfer type** (manual ↔ autoload) on a recorded leg changes which milestones
  the leg should have had (a manual sale has a separate unload step). Excluded from the
  first edit slice; is it ever needed?
- **Hot cargo.** Does it need a flag so lookups only suggest terminals that will take it?
  Depends on whether UEX exposes that.
- **What a mining or salvage session actually looks like** for Jeff — refine then haul, or
  sell raw? Several refinery jobs per session? RMC picked up in stages? Refinery jobs take
  hours, a wait trading doesn't have; that may shape the sell-only model more than trading
  did. Ask before designing.
- **Carry-over** of a remainder into a later run, with its cost basis — only if real use
  asks for it.
