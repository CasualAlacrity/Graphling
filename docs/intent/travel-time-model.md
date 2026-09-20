# Intent — a travel-time model that covers the whole flight

**Status:** not started (2026-09-18). Raised after a measurement showed the current model
can't distinguish a surface terminal from the station in orbit above it.

## Problem

Profit per hour is `profit / (transfer_time + travel_time)`, so the travel estimate
decides the rate, and a systematic undercount on one *kind* of flight biases the rate
toward that kind.

As of 2026-09-20 `best_route` ranks by **profit per run** by default precisely because
that measure never touches this model — see `rank_by` in `best_route_tool.py`. Per-hour
is still offered when a pilot asks for it, and still feeds `trade_advisor`'s comparison
against a committed run, so this remains load-bearing rather than optional. Fixing it is
what would let per-hour become a trustworthy default again.

The current model measures **horizontal distance only**. Measured 2026-09-18:

```
TDD - Cloudview Center - Orison  ->  Admin - Seraphim   =  0.1 min
```

Orison is on a platform in Crusader's atmosphere; Seraphim is the station in orbit above
it. In game that's a climb out of atmosphere. The model returns six seconds, because both
share the Crusader orbit and orbit-to-orbit distance is zero.

The consequence isn't just a wrong number. When `best_route` searches a region it ranks
surface and orbital terminals against each other, and orbital-to-orbital runs — Seraphim
to a Gateway station — win partly because the model never charges them for the phases they
skip. A pilot standing on the platform gets a route whose quoted profit/hour omits their
climb.

This also blocks scoring an approach leg ("you're at TDD, this route starts at Seraphim").
There's no point adding that term while the underlying estimate for it is ~0.

## What already exists — don't rebuild it

`app/tools/travel_time.py` is more developed than it looks:

- Wiki `locations/positions` coordinates as the primary distance source, validated to the
  decimal against a real route.
- UEX orbit-to-orbit distance as fallback when coordinates are missing for one end.
- Cross-system routing via jump points — sums the QT-cruise legs either side of the jump.
- `travel_time_confidence()` and `_is_orbital()` / `is_same_physical_location()`, so the
  code can already *tell* when it's estimating a flight phase it doesn't model.

The scaffolding to flag a low-confidence estimate is there. What's missing is the actual
durations.

## Desired outcome

An estimate that accounts for every phase of a real trip, not just the quantum cruise.

Missing phases. Walking one real arrival at Orison, in order:

1. **Quantum cruise** — the only phase modelled today.
2. **QT drive cooldown, then spool-up** — fixed overhead paid per jump, not per km. A
   route with several hops pays it several times.
3. **Post-QT sublight approach** — you drop out 25–50 km short and fly the rest on
   normal thrust. Distance-proportional but at a completely different speed to QT.
4. **Atmospheric entry and descent** — arriving at Orison puts you above Crusader, and
   the descent is its own phase. Not symmetric with the ascent leaving.
5. **Surface travel to the dock** — another ~25 km once you're in atmosphere.
6. **Cargo transfer** — and *auto-load has its own duration*, distinct from the manual
   crate-handling model in `cargo_packing`. `CargoTransferType` already distinguishes
   AUTOLOAD from MANUAL on the ledger; only the manual path has a time estimate.

Each one is small. Together they're the difference between a modelled 1.9 minutes and
what the trip actually takes.

Also missing: **jump gate transit** for cross-system routes, currently excluded outright —
the docstring notes it isn't distance-proportional the way in-system QT is, and the wiki's
own route planner shows no number for that phase either.

## Constraints

- **Approximate is fine; systematically biased is not.** Profit/hour is an estimate and
  always will be — it assumes plenty that pilot skill can negate, so no amount of
  modelling makes it exact for a given person. It only has to be good enough for an
  apples-to-apples comparison between routes.

- **Therefore: only model phases that differ between candidates.** A phase every route
  pays equally cancels out of the ranking and can be ignored entirely, however real it is
  in the cockpit. That's the test for whether each phase above is worth the effort:
  atmospheric entry matters because only surface destinations pay it, and jump transit
  matters because only cross-system routes do. QT spool-up, paid once per hop by every
  route alike, is closer to a constant — worth modelling only if hop *counts* differ
  between candidates, which they do on multi-jump routes.
- Phase durations are likely ship-dependent (mass, quantum drive, atmospheric handling),
  so a single constant per phase may not survive contact with a Hull C versus a Cutlass.
- Whatever lands must keep the `float | str` contract — callers treat a string as "can't
  estimate" and drop the candidate, which is how a resolution failure silently removed
  routes from ranking once already.

## Open questions

- **Where do the numbers come from?** They're measurable in game, and Jeff's own hauling
  sessions are the obvious source — timing a few ascents, descents, jumps and hangar
  approaches would produce real figures faster than hunting for published data. Worth
  capturing during an op night rather than as a separate exercise.
- Per-ship variation: one constant, a per-ship-class table, or a function of mass/size?
- Does the existing confidence signal become a *correction* (add a modelled penalty) or
  stay a *caveat* (tell the pilot the number is a floor)? The first improves ranking; the
  second is honest without pretending to precision we haven't measured.
- Does jump transit vary by gate, or is it effectively constant?
