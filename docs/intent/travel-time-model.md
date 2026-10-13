# Intent — a travel-time model that covers the whole flight

**Status:** partly built (2026-10-13) — quantum cruise, QT drop-out and the sublight
approach are modelled from game data; atmosphere, surface travel and jump transit are
not. See "Built 2026-10-13" below. Raised 2026-09-18 after a measurement showed the
model couldn't distinguish a surface terminal from the station in orbit above it.

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

## Built 2026-10-13

All in `app/tools/travel_time.py`, data from the star-citizen.wiki API:

- **Quantum cruise with the drive's acceleration stages** (stage one to engage speed,
  stage two to full speed, mirrored to slow down) — the installed drive's own item stats,
  fetched alongside the ship. Short hops were the worst undercount: a Railen's 2 Gm moon
  hop went from ~15s to ~40s.
- **QT drop-out at the destination's arrival radius** — game data's
  `quantum_travel.arrival_radius` from the wiki's v2 locations list, joined onto the
  positions data by uuid. Trusted over hand-kept numbers so new locations come for free:
  stations 24 km, cities and outposts 20 km, Levski 60 km.
- **Sublight approach** from drop-out — full thrust to NAV max, cruise, full retro down,
  from the ship's 0-to-max / max-to-0 times.
- **Gateways:** pilots QT to the gateway station, not the jump point. Approach is
  modelled as the station's arrival radius plus station-to-point distance in a straight
  line (~60–75 km): jump points are the outermost structures in a system, so the drop-out
  is effectively always on the star side. Not the exact vector, but consistent across
  routes, which is what ranking needs.
- **Fuel:** QT fuel as SCU per Gm (capacity / range — cross-checked against the drive's
  own SCU/Gm figure). Hydrogen burn while thrusting, in SCU — **unit of the wiki's
  `fuel.usage` figures not confirmed**; `HYDROGEN_USAGE_UNIT_SCU` assumes millionths
  pending a measured in-game burn.

`estimate_trip` returns the breakdown (`TripEstimate`); `estimate_travel_time` stays the
`float | str` total every ranking caller uses.

**Not surfaced to the pilot yet, on purpose.** The breakdown and fuel figures exist but
`estimate_travel_time` the tool still answers only "how long". Answering "how much fuel
does that burn?" or "why so long?" is a different question, and it's not settled that
it should be a tool at all (vs. an argument on this one, or some other structure) —
open question below.

## Desired outcome

An estimate that accounts for every phase of a real trip, not just the quantum cruise.

Missing phases. Walking one real arrival at Orison, in order:

1. **Quantum cruise** — modelled, with acceleration stages (2026-10-13).
2. **QT drive cooldown, then spool-up** — fixed overhead paid per jump, not per km. A
   route with several hops pays it several times.
3. **Post-QT sublight approach** — you drop out 20–60 km short and fly the rest on
   normal thrust. Modelled 2026-10-13 from each location's arrival radius.
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
- **How do fuel, distance and breakdown questions get answered?** "Do I have the fuel to
  reach Port Tressler?", "how far is it to Port Tressler from Orison?" and "why does that
  take so long?" — the data exists in `TripEstimate`. (Distance from wherever the pilot is
  standing is the game UI's job; ALICE only knows their location via an active run.)
  Note `estimate_travel_time`'s description already lists "how far is Seraphim" as a
  trigger but answers with a time — a mismatch until this is settled.
  Candidates: a separate tool, an argument on `estimate_travel_time` that says which
  part was asked about (like `best_route`'s `rank_by`), or something that isn't a tool.
  Not decided — evaluate before building. Whatever it is must answer only what was
  asked; dumping time + breakdown + fuel into one spoken answer is what to avoid.
- **Hydrogen unit and approach profile need one playtest measurement each** — hydrogen %
  over five 0→max→0 cycles in a Railen (model expects ~1%), and a stopwatch on a short
  moon hop (model expects ~0:40 in quantum from CRU-L1 to Seraphim).
