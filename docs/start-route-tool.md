# `start_trade_run` tool — design

**Status:** design, not built. Closes the high-priority gap in `trade-route-tracker.md`
("missing 'commit to this route' tool"). Jeff implements (AI-engineering layer); this doc
is the spec + shape.

## The gap

`best_route` (and `trade_advisor`) recommend a route. The pilot says "yes, do that" — and
there's no tool that turns a recommendation into a committed `TradeRun`. Live traces show
the model re-running `best_route` or improvising with `start_timer`. `create_run_from_route`
in `app/db/trade_run_store.py` is exactly the missing piece; the overlay calls it
(`overlay_canvas.py:62`), nothing wraps it for ALICE.

## Decision: stateless (Option A)

The tool takes the route by **name** (origin, destination, commodity, ship, quantity),
re-resolves each against UEX, re-fetches the route row, and calls `create_run_from_route`.
No session/graph state.

Why, over stashing `best_route`'s last result in thread state (Option B):

- Every other tool in the codebase is stateless + resolver-based (`resolver.py`,
  `resolve_or_hedge`). Option A matches that; Option B introduces a new state mechanism
  and needs `thread_id` plumbed into a tool (currently tools get no `RunnableConfig`).
- Deterministic and testable — the tool-selection harness (roadmap Phase 3) can exercise
  it with plain args, no need to simulate a prior `best_route` call.
- Works for the direct case too: "start a run hauling Titanium from Seraphim to Baijini
  Point in my Cat" — no prior recommendation needed.
- The model already has every arg: `best_route`'s reply says "Best from **Seraphim** in
  the **Caterpillar**: 522 SCU of **Titanium** to **Baijini Point**".

Downside accepted: prices can move between the suggestion and the commit (re-fetch gets
current numbers, which may differ slightly from what the pilot heard). That's correct
behaviour, not a bug — the run should record what's true at commit time. If the model
turns out to struggle carrying the args, add Option B later as a zero-arg fast path; don't
build it pre-emptively.

## Name & location

- Tool name: `start_trade_run` (verb consistent with `mark_*` / `confirm_*`; "run" makes
  clear it creates a ledger record, not just picks a route).
- File: `app/tools/trade_run/start_trade_run_tool.py`, following `mark_arrived_tool.py`.
- Class: `StartTradeRunTool(UplinkTool)` — needs `client: UEXCorpClient` (route fetch +
  cache), so constructed like `CargoPackingTool(client=uex_client)` in `graph.py`.

## Arg schema

```python
class StartTradeRunArgs(BaseModel):
    origin: str        # "where the pilot is buying" — terminal name, may be phonetic
    destination: str   # "where they'll sell" — terminal name, may be phonetic
    commodity: str     # commodity name, may be phonetic
    ship: str | None = None        # falls back to a single active run's ship? no —
                                   # there's no active run yet. If unset, ask.
    quantity_scu: int | None = None  # unset -> fill the ship's reachable capacity
```

Same field-description discipline as `MarkArrivedArgs`: tell the model these may be
misspelled / phonetically transcribed and to pass through what the pilot said, not
self-correct.

## Resolution flow

1. `cache = await self.client.get_uex_cache()`
2. Resolve each name with `resolve_or_hedge` (from `tools.uexcorp.matching`), returning
   the hedge message immediately on `(None, message)`:
   - `origin`  → `cache.terminals`, `"location"` (default `WRatio` scorer)
   - `destination` → `cache.terminals`, `"location"`
   - `commodity` → `cache.commodities`, `"commodity"`
   - `ship` → `cache.vehicles`, `"ship"`, `scorer=fuzz.token_sort_ratio` (the
     whole-string scorer — see the comment in `match_by_name_or_code_with_score`)
   - if `ship is None`: return `"Which ship are you flying?"` (no active run to inherit
     from, unlike the other trade-run tools)
3. Fetch the route row:
   `rows = await self.client.get_commodity_routes(commodity_id=matched_commodity.id,
   origin_terminal_id=origin.id, destination_terminal_id=destination.id)`
   - `[]` → return a plain miss: `"No current route for {commodity} from {origin} to
     {destination} — prices or stock may have dried up."` (this is also what you get if
     the pilot misremembers the pair — acceptable.)
   - normally exactly one row; if >1, take the first (same commodity + terminal pair).
4. `route = UEXTradeRoute.model_validate(rows[0])`
5. **Patch autoload** — `is_auto_load_origin` / `is_auto_load_destination` are **not** in
   the `commodities_routes` payload; they default to `0`, and `create_run_from_route`
   reads them to choose `CargoTransferType`. Without this, every leg silently becomes
   `MANUAL`. `uex_lookup.search_routes` already does this patch via `model_copy(update=…)`
   using a terminal lookup; reuse the same idea:
   ```python
   route = route.model_copy(update={
       "is_auto_load_origin": _terminal_is_auto_load(cache, route.origin_terminal_id),
       "is_auto_load_destination": _terminal_is_auto_load(cache, route.destination_terminal_id),
   })
   ```
   `route_ranking._terminal_is_auto_load(cache, terminal_id)` already exists with this
   exact signature — consider promoting it out of the private namespace so the tool and
   `route_ranking` share one copy (there are currently two: this one and
   `uex_lookup._terminal_is_auto_load`).
6. Quantity: `qty = quantity_scu or reachable_scu(route, int(matched_vehicle.scu))`
   (`reachable_scu` from `tools.cargo_packing` — the same DP-based true-capacity figure
   `best_route` and the overlay use). Guard `qty <= 0` → `"That route can't fill any
   cargo right now — origin or destination stock is at zero."`
7. Existing runs: `active = await trade_run_store.get_in_progress_runs()`. Don't block —
   multiple concurrent runs are allowed and `resolver` disambiguates downstream — but
   include the count in the reply if `active` is non-empty so the pilot isn't surprised.
8. `run = await trade_run_store.create_run_from_route(route, qty, matched_vehicle.name)`
   wrapped in `self._safe_run(...)`.

Cross-system routes need no special handling here — `create_run_from_route` is pure data
assembly. (Travel-time estimation is a separate concern and already degrades gracefully
elsewhere.)

## Philosophy constraint (HCI design-rationale doc, 2026-09-10)

"ALICE presents information and options; the pilot makes the choices. No Auto-Play."
`start_trade_run` is on the right side of this only because it requires an **affirmative
pilot choice** — "yes, do that", "start the run", or a fully-specified direct request. The
model must never call it speculatively off its own recommendation, and the description
must make that explicit (see below). The tool executes a decision the pilot already made;
it does not make one.

## Structured display payload (for the presence layer)

Alongside the prose return, the tool emits a `tracker_advance` / `run_started` display
intent carrying structured fields, not text — the new run id, both legs
(type / terminal / commodity / qty / transfer type), and the first step. See
`docs/presence-layer.md` "From the HCI design-rationale doc".

## Reported message

Confirm what was committed and point at the first action, mirroring
`trade_run_store.trade_run_info` / `current_step_title` phrasing so it reads as status,
not as a command to chain another tool:

> "Run started — {qty} SCU {commodity}, {origin} → {destination} in the {ship}. You're
> traveling to {origin} to buy. Mark arrived when you land."

If `active` was non-empty: prepend "That's your {n+1} active runs. "

## graph.py wiring

Add to `trade_run_tools`:
```python
start_trade_run_tool = StartTradeRunTool(client=uex_client)
trade_run_tools = [..., start_trade_run_tool]
```
Tool count goes 17 → 18 (update the README note).

## Tool description (the part that drives selection)

Must fire on commitment after a recommendation ("yes", "let's do it", "start that run",
"commit to the Titanium run") **and** on a fully-specified direct request ("start a run
hauling X from A to B in my Cat"). Must **not** fire on a hypothetical ("what if I
hauled…") or a bare route question (that's `best_route`). Call out that it creates a
tracked run with buy + sell legs.

Companion change (optional, small): `best_route`'s reply currently just ends on the
recommendation. Add a closing line — "Say the word to start this run." — so the model has
a reason to know the affordance exists. This is the "minor self-documentation" note in
`trade-route-tracker.md`.

## Tests to add

`tests/tools/` (there's already `test_trade_run_store.py`, `test_route_fanout.py`):

- happy path: names resolve, route fetched, run created with both legs, correct
  `CargoTransferType` per the autoload patch
- `quantity_scu` unset → filled from ship capacity; explicit value respected
- low-confidence name → hedge message, no run created
- no route for the pair → miss message, no run created
- autoload patch: a route whose destination terminal is autoload-capable produces an
  `AUTOLOAD` sale leg (this is the regression the patch exists to prevent)
- fake/record the `get_commodity_routes` + `get_uex_cache` calls (ties into the Phase 3
  deterministic-external-layer work — this tool is a good first customer for it)

## After it lands

- Update `trade-route-tracker.md` "Known gaps" — remove the commit-tool gap.
- Update the README tool count.
- `docs/todo.md` Phase 1 — check the box.
