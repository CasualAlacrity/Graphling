# Intent — a travel-time model that covers the whole flight

**Status:** not started (2026-09-18). Raised after a measurement showed the current model
can't distinguish a surface terminal from the station in orbit above it.

## Problem

Profit per hour is the ranking criterion for every route ALICE recommends, and the
headline number she says out loud. It's `profit / (transfer_time + travel_time)`. So the
travel estimate isn't a detail — it decides which route wins, and a systematic undercount
on one *kind* of flight biases every recommendation toward that kind.

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

Missing phases, as named by Jeff:

- **Surface → orbit.** Atmospheric ascent from a landing zone.
- **Orbit → surface.** Entry and descent, which is not symmetric with ascent.
- **Jump gate transit.** Currently excluded outright — the docstring notes it isn't
  distance-proportional the way in-system QT is, and the wiki's own route planner shows no
  number for it either.
- **Hydrogen burn, orbit → hangar.** The slow non-quantum approach and docking phase,
  which is pure overhead no distance figure captures.

## Constraints

- **Approximate is fine; systematically biased is not.** Profit/hour is an estimate and
  always will be — it assumes plenty that pilot skill can negate, so no amount of
  modelling makes it exact for a given person. It only has to be good enough for an
  apples-to-apples comparison between routes.

- **Therefore: only model phases that differ between candidates.** A phase every route
  pays equally cancels out of the ranking and can be ignored entirely, however real it is
  in the cockpit. That's the test for whether each of the four below is worth the effort —
  surface→orbit matters because only surface origins pay it, and jump transit matters
  because only cross-system routes do. A hangar approach every destination requires is
  a constant, and constants don't change which route wins.
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
