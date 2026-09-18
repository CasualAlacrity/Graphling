# `start_trade_run` tool — design (built)

**Status:** built 2026-09-11. Closes the gap in `trade-route-tracker.md` ("missing
'commit to this route' tool"). Superseded the original name-based design below the fold
of this doc's history — see git history on this file if the earlier resolve-by-name
approach ever needs revisiting.

## The gap

`best_route` (and `trade_advisor`) recommend a route. The pilot says "yes, do that" — and
there's no tool that turns a recommendation into a committed `TradeRun`. Live traces show
the model re-running `best_route` or improvising with `start_timer`. `create_run_from_route`
in `app/db/trade_run_store.py` is exactly the missing piece; the overlay calls it
(`overlay_canvas.py:62`), nothing wraps it for ALICE.

## Decision: hand off the resolved route, don't re-describe it

The first design re-resolved origin/destination/commodity/ship by name and re-fetched the
route from UEX inside `start_trade_run` itself. Reopened in review: `best_route` already
did all of that resolution to produce its recommendation — re-doing it means duplicate
work *and* reintroduced risk (a second fuzzy-match pass could land on a different terminal
than the one just reported; a re-typed SCU figure could drift from the one actually
computed). "Best_route_tool returns a route based on ALICE's logic and constraints;
start_trade_run_tool starts the route" — division of labor, not two independent
resolutions of the same thing.

**Mechanism:** `best_route` (and `trade_advisor`, if it ever offers a "start this instead"
action) stashes the resolved `(UEXTradeRoute, scu, vehicle_name)` in
`app/tools/trade_run/route_cache.py` — an in-memory, single-process dict, same shape as
`voice/timer_tool.py`'s `_timers` — and returns a short opaque token in its reply. The
model carries that token in context (it's marked "don't say this part aloud," so it never
gets spoken) and passes it to `start_trade_run` on commitment. No re-resolution, no UEX
client needed in `start_trade_run` at all.

Direct, from-scratch requests ("start a run hauling titanium from Seraphim to Baijini in
my Cat") are **not** handled by `start_trade_run` — the model runs `best_route` first to
get a token, same as any other commitment. One tool, one job: commit an already-found
route.

### Every route named out loud gets a token (fixed 2026-09-18)

`best_route` describes a runner-up when it finds one, but originally stashed only the
winner. ALICE would name a second route the pilot had no way to choose: "do that one
instead" had nothing to resolve to, because `start_trade_run` can only reach a route that
was stashed, never one that existed solely in prose.

Two fixes, both required:

- `best_route` now stashes the runner-up as well and carries **both** tokens in its
  internal note, each paired with its commodity name so the model passes the one the pilot
  actually picked.
- `find_best_route` now returns `runner_up_scu` alongside `runner_up`. It previously
  tracked the load size only for the winner, so even with a token the runner-up couldn't
  be committed — and re-deriving that figure later would recompute against whatever ship
  state exists then rather than the one it was ranked for.

**Standing rule:** any route ALICE mentions aloud must be selectable. Describing an option
without stashing it is offering something the system can't act on.

## Shape (as built)

```python
class StartTradeRunArgs(BaseModel):
    route_token: str            # from a best_route/trade_advisor reply; never invented
    quantity_scu: int | None = None   # override; unset -> the SCU already computed for it

class StartTradeRunTool(UplinkTool):   # not UEXBackedTool — no UEX client needed
    async def _start(self, route_token, quantity_scu):
        cached = route_cache.get(route_token)
        if cached is None:
            return "That recommendation isn't available anymore — want me to search again?"
        route, scu_hint, vehicle_name = cached
        qty = quantity_scu or scu_hint
        # guard qty <= 0, note existing in-progress run count, create_run_from_route, report
```

`app/tools/trade_run/start_trade_run_tool.py` is the real implementation; this is the
shape summary.

## The autoload bug this surfaced

`find_best_route` (`app/tools/route_ranking.py`) was never patching
`is_auto_load_origin`/`is_auto_load_destination` on the routes it returns — only
`uex_lookup.search_routes` (the overlay's separate implementation) did that patch. Any
route handed straight to `create_run_from_route` would have silently produced `MANUAL` on
both legs regardless of the terminal's real capability. Fixed at the source: `find_best_route`
now patches both `best` and `runner_up` before returning, so every caller (`best_route_tool`,
`trade_advisor_tool`) gets it for free — not something each caller needs to remember.

## Philosophy constraint (HCI design-rationale doc, 2026-09-10)

"ALICE presents information and options; the pilot makes the choices. No Auto-Play."
`start_trade_run` only ever executes a choice the pilot already made — it requires a
`route_token` from a prior recommendation, so there's no path for the model to call it
speculatively off its own suggestion. `best_route`'s description explicitly tells the
model to pass the token, never to re-describe the route itself.

## Structured display payload (for the presence layer, not yet built)

Alongside the prose return, the tool should eventually emit a `tracker_advance` /
`run_started` display intent carrying structured fields — the new run id, both legs
(type / terminal / commodity / qty / transfer type), and the first step. See
`docs/presence-layer.md`. Not part of this pass — the presence layer itself isn't built.

## graph.py wiring (done)

```python
start_trade_run_tool = StartTradeRunTool()
trade_run_tools = [..., start_trade_run_tool]
```
Tool count: 17 → 18.

## Tests still to add

`tests/tools/` (there's already `test_trade_run_store.py`, `test_route_fanout.py`):

- happy path: token resolves, run created with both legs, correct `CargoTransferType`
  (regression coverage for the autoload fix above)
- unknown/expired token → the "isn't available anymore" message, no run created
- `quantity_scu` override respected vs. the cached `scu_hint` used when unset
- zero-SCU guard
- `route_cache.stash`/`get` round-trip
- `best_route` emits two distinct tokens when a runner-up exists, and the runner-up's
  token resolves to the runner-up route

**Added 2026-09-18** — `tests/tools/test_route_ranking.py`, the first test to touch
`find_best_route` at all:

- `runner_up_scu` is the runner-up's own reachable load, not the winner's, in both the
  branch where the runner-up is seen second and the branch where it is seen first and then
  demoted. Both fail against the pre-fix code (`None` rather than the expected load), which
  was verified by reverting the fix rather than assumed.
- **both routes reaching the same load** — the ordinary case, since two routes with stock
  to spare both fill the ship. Nothing in the ranking requires the loads to differ; the
  stock-limited fixtures elsewhere in that file exist only to catch a `runner_up_scu =
  best_scu` shortcut, which would satisfy every equal-load assertion forever.
- single candidate leaves every runner-up field `None`
- `is_auto_load_origin/destination` come back patched on both `best` and `runner_up` —
  retroactive coverage for the autoload bug above, which had none

Ties into the Phase 3 deterministic-external-layer work — this tool (and `best_route`'s
token production) is a good first customer for it.

## Follow-up affordances discussed, not built

Raised while designing this: whether a generic `search_for_routes_tool` is needed for
cases `best_route` doesn't cover. Decision: no generic search tool — see
`trade-route-tracker.md`'s Trade Advisor section for the two named affordances that came
out of that discussion instead (a free ergonomic default on `best_route`, and a genuinely
new `find_detour_pickup`-shaped tool for the SCU-shortfall "second pickup stop" case).
