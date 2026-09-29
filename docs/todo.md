# Graphling — working todo

Direction set 2026-09-09/10 (where-are-we review + scope + product-framing conversation):

1. **Finish the trade route tracker — working end to end by voice.** Nothing else starts
   until one real trade run can be committed, advanced, and finalized start-to-finish.
2. Multi-tenancy — shared backend, per-user client. This is the **data-pool foundation**
   for the product thesis below, not infra for two people.
3. Tool-selection test harness — before the tool count grows.
4. Grow tools / functionality, staying SC-focused, chosen so they'd also serve a future
   companion-assistant ALICE.
5. Tool-selection improvements, on the back of the harness.
6. Star Citizen content polish pass.

Then (own phase, not yet scheduled): **the crowd-sourced trade-data pipeline** — OCR the
commodity kiosk to capture station buy/sell prices + stock as pilots trade, pool it
server-side, serve to-the-minute data to every ALICE user. The product's moat: UEX/StarHead
have a structural 24–48h trust-gated delay, so ALICE contributes upstream freely while its
users keep the freshness edge. Revenue = selling advanced ALICE feature access, not data.
Later: pool outlier-detection for when it opens past hand-picked seed users. Ties to
memories `project-product-thesis`, `project-trade-data-freshness`, `project-vlm-ocr-vision`.

**Framing (2026-09-10):** still a learning project (the AI engineering is real and the
point), now also a **prospective product / side hustle**. "Demoable" is now also "people
could pay to use it". Doesn't change the next step (finish the loop) but raises the stakes
on multi-tenancy being done properly.

The presentation is done — no more demo/slide work.

Running alongside the numbered phases: the **presence layer** UI track — making ALICE's
voice responses visible (mini tracker, on-screen timer, route cards). Full design in
`docs/presence-layer.md`. Mostly Claude's to build (Qt), with small tool-side event hooks
(Jeff's).

**One package, one run mode.** "Just ALICE" = voice loop + PySide6 overlay + presence
layer, co-resident in one process, launched one way. Chainlit is cut. No chat-only, no
voice-only-without-UI, no UI-without-voice. `PTT_MODE=text` stays as a dev/no-mic
fallback, not a mode.

**North star (not a v1 feature):** one ALICE personality. Long-term she's a personal
assistant / companion who moonlights as an SC copilot. For now the build stays SC-focused;
new tools just get picked with "would this also help assistant-ALICE?" as a tiebreaker.
The SC-vs-general router / mode-split is explicitly deferred — see "Parked".

Reminder on how work splits (memory `feedback-ask-before-editing`): AI-engineering code
(graph nodes, tools, prompts, structured-output schemas) — Claude diagnoses and sketches
the shape, Jeff writes it. PySide6/overlay, infra, schema/migrations — Claude implements
directly.

---

## Phase 1 — Finish the trade route tracker (close the AI loop)

- [x] **`start_trade_run` commit tool** — built 2026-09-11. Not the stateless
      resolve-by-name design originally sketched here: `best_route` stashes its resolved
      route in `app/tools/trade_run/route_cache.py` and returns a token; `start_trade_run`
      just looks it up and commits, no re-resolution. See `docs/start-route-tool.md`.
      Also fixed a real bug this surfaced: `find_best_route` wasn't patching
      `is_auto_load_origin/destination` on its returned routes (`route_ranking.py`).
    - [x] **Tests added 2026-09-22** (`5ae0736`) — all 6 cases from `docs/start-route-tool.md`
          "Tests still to add": `tests/tools/test_start_trade_run_tool.py` and
          `tests/tools/test_best_route_tool.py` (the runner-up-token case, which turned
          out to be `best_route_tool`'s concern, not `start_trade_run`'s).
    - [x] **First live voice test run, completed 2026-09-22.** best_route → start_trade_run
          → mark_arrived → mark_cargo_acquired → confirm_cargo_loaded → mark_arrived (sale
          leg) → mark_cargo_sold → Finalize (both legs, manual, in the overlay) — full loop,
          voice to ledger. Verified in the DB: run `474397f7` finalized 08:02:55 UTC, 640
          SCU Copper, Admin - Seraphim → Admin - Rod's Fuel 'N Supplies, Railen. Found real
          bugs along the way (number pronunciation, missing finalize nudge, canned decline
          lines, markdown creeping into spoken replies, `classify_topic` misfires, and the
          ~30s cold-start latency) — first three fixed same day, rest logged above and in
          `classify-topic-rework.md`.
    - [x] **Unselectable runner-up fixed 2026-09-18.** `best_route` named a second route
          but stashed only the winner, so "do that one instead" had nothing to resolve to;
          `find_best_route` also never returned the runner-up's SCU, so it couldn't be
          committed even with a token. Both fixed — see `docs/start-route-tool.md`.
          Standing rule that came out of it: any route ALICE mentions aloud must be
          selectable.
- [x] **Decide Trade Advisor's fate — re-added as-is, 2026-09-22** (`60f002e`). 19 tools
      now bound.
- [x] **Overlay ↔ AI shared-state check — verified 2026-09-22.** Two refresh triggers
      exist (`overlay_canvas.py`): tab-switch and the F3 toggle-open, the latter's
      docstring explicitly naming this exact scenario ("an AI trade-run tool ran while
      the overlay was hidden"). No live push while the panel is already open and sitting
      on that tab — not a gap, that's exactly what Presence Layer Tier 2's event bus is
      for, and the overlay is a toggle-open/close UI by design, not a persistent HUD.
- [x] **Manual/AI parity pass — done 2026-09-22.** Every milestone has two-way parity
      (arrive, buy/sell with overrides, confirm loaded/unloaded, start a run) except one:
      **Abandon Run has no voice equivalent** — overlay-only, hard-deletes via
      `delete_run`. **Decided 2026-09-24: staying that way, on purpose.** Abandoning a
      run is purely a pilot-run action — no voice path, no confirmed-only exception. Not
      implicit anymore.
- [ ] **URGENT — recorded leg values can't be edited (found 2026-09-29).** Once a
      purchase/sale is recorded — overlay *or* voice — it's permanent: `record_purchase`/
      `record_sale` (`app/server/ledger_service.py`) refuse a second record, no update
      endpoint exists, Mark Done and Finalize Run show read-only recaps
      (`build_recap_grid`), and Abandon disappears once anything is bought
      (`_can_abandon`). The overlay Buy/Sell form before submit is the only edit window,
      and the voice path skips it entirely — so one Whisper mishearing (614 for 640) is a
      permanent wrong ledger entry today. Blocks the voice readback decided in
      `docs/intent/voice-latency.md` ("640 at 14.2, logged" is pointless if "no, 614" has
      nowhere to go).
    - [ ] **Ledger edit path — server + overlay** (Claude implements, per the split above).
          **Spec: `docs/ledger-edit-path.md`**; wider design: `docs/intent/run-model.md`.
          Quantity, price, transfer type, and fee editable on any recorded leg **until the
          run is finalized**: from the leg's Mark Done review and from the Finalize Run
          step, both legs. Run Finalize stays the lock point and stays manual-only.
    - [ ] **Voice correction tool** on top of it (Jeff's — tool + schema). "No, it was 614"
          / "actually 14.5 a unit" right after a readback. Scope from
          `docs/ledger-trust-and-corrections.md`'s voice-corrections design, cut down to
          editing recorded values — not the shelved per-value provenance system.
    - **Decided 2026-09-29 (Jeff):**
        - **A bought quantity carries forward to the sale leg.** Correcting (or recording)
          the buy as 614 means the sale leg's planned quantity is 614 — you can only sell
          what you bought. Not true today even without edits: `_apply_transaction` only
          touches its own leg, so buying 614 against a 640 plan leaves the sale leg at
          640, and "sold it all" / the Sell form default to 640.
        - **Editing a done leg keeps it done** — only its values change. Done legs must be
          editable, or the Finalize Run step is a rubber stamp with nothing to review.
    - [ ] **Quantity reconciliation before Finalize Run.** Bought 640, sold 614 → the run
          isn't finished; 26 SCU are unaccounted for. Today nothing checks this: the Sell
          quantity box allows up to 100,000 and the server never compares legs, so selling
          *more* than was bought records fine and inflates profit. Needs:
          - sold > bought → rejected (or forces a correction of one leg);
          - sold < bought → Finalize Run blocked until the remainder is accounted for.
          **Why a remainder exists — Jeff, 2026-09-29:**
          1. *Wrong buy amount, no real cargo left* → a ledger edit before Finalize; this
             is exactly what the manual Finalize step is for (edit path above).
          2. *Waiting to sell here later* — most likely the terminal's demand filled up
             mid-sale. Wait-vs-move-on stays the pilot's call (`trade_advisor` already
             refuses to weigh in on it).
          3. *Selling the rest somewhere else* — and probably asking ALICE where.
          Plus one Claude added: 4. *Cargo is gone* (death, piracy, crash) — common in
          SC, and currently unfinalizable since Abandon disappears after a purchase.

          **Resulting model (proposed):** a run is **one buy leg + N sale legs**;
          remainder = bought − Σ sold; the run stays open (in progress, remainder shown)
          while remainder > 0; **Finalize Run requires remainder = 0**. Cases 2 and 3 are
          both "add another sale leg" (same terminal, or a new one); case 4 is a
          **write-off** with a reason — kept only for real loss, not as the general way to
          close a remainder. This replaces "only ever one sibling per run"
          (`advance_leg`'s sibling-start logic, overlay leg rendering, profit math all
          assume it) and also closes the "sold 300, keeping the rest" partial-sale gap.
          - [ ] Schema + server + overlay for N sale legs and remainder (Claude).
          - [ ] Write-off for lost cargo (Claude, overlay; voice path TBD — same
                pilot-only question as Abandon).
          - [ ] AI: "where can I sell the other 26?" + "add a sale leg there" (Jeff —
                tools). Close cousin of `find_detour_pickup`, which also appends a leg to
                an existing run.
    - [ ] **Sell-only runs — a run with no buy leg** (Jeff's idea, 2026-09-29). For cargo
          that never came from a terminal: miners (refined ore), salvagers (RMC/CMAT),
          pirates (hot cargo, sellable only at no-questions-asked terminals). Opens the
          ledger to whole player groups the buy→sell model excludes. Same model as above,
          generalized: every run = **a cargo source + N sale legs**, profit = Σ sales −
          cost basis. Source is either a buy leg (cost basis = price × SCU) or
          mined/salvaged/looted (cost basis 0). Not a separate trade type — a run whose
          source isn't a purchase.
          - Kept distinct from leftover trade cargo: a trade run's remainder gets another
            sale leg *on the same run*, so one trade keeps one honest profit figure.
            Moving a remainder into a fresh sell-only run would book a loss on the
            original and a fake 100% margin on the new one. A carry-over (remainder moves
            to a later run *with* its cost basis) is possible later if "sell it next
            session" turns out to be common — not needed now.
          - AI side already half-exists: `commodity_price_lookup`'s `best_to_sell` answers
            "where do I sell 32 SCU of Quant?" — what's missing is committing it as a run
            (a sell-only `start_trade_run` path, Jeff's).
          - Open: does "hot" cargo need its own flag, so lookups only suggest terminals that
            will actually take it? Depends on whether UEX exposes that.

## Phase 2 — Multi-tenancy (shared backend, per-user client)

**Shape changed 2026-09-25 — server-mediated, not direct-DB.** Originally "one shared
Postgres both pilots reach directly, each running their own client" — but that raises a
real question direct-DB access can't answer cleanly: how does a client on someone else's
machine reach a database that isn't on theirs? The two candidates were a VPN (Tailscale)
or a plain VPS with a published Postgres port; both were rejected — Tailscale requires
manually inviting every future pilot onto a private network (doesn't scale past "people
I personally know", and isn't the shape a real paid product would ever want a stranger
using anyway), and a bare published port is real exposure for a password-protected
database. The actual fix: a small FastAPI server (`app/server/`) mediates every ledger
read/write over HTTPS, authenticated by login (Discord OAuth + JWT), not network
location. Postgres then never needs to be reachable from outside its own box at all —
same shape as Flockt's own `db` container. Modeled on Project Lyra and Uplink (two
earlier projects, same author, same Discord-OAuth-relay pattern), specifically for the
security property they already have: the *server* verifies identity with Discord using
a real client secret, not "the client says who it is." Full design/build order in the
plan this was built from; deployment mechanics in `docs/deploy.md`.
- [x] **`app/server/` built and tested — 2026-09-25.** `ledger_service.py` (the
      DB-touching functions that used to live in `db/trade_run_store.py`, now taking
      `user_id` explicitly since a server serves concurrent requests from potentially
      different pilots — a process-global only ever worked for "one process = one
      pilot"), `routes/ledger.py` (one route per function, behind
      `Depends(get_current_user)`), `routes/auth.py` (the Discord OAuth relay — the
      client's desktop app can't sit at the server's origin like a browser tab would, so
      the server bounces the browser back to the client's own local listener with a JWT
      once its Discord exchange is done), `dependencies.py` (JWT → `User`, modeled on
      Lyra's `api/dependencies.py`). `db/trade_run_store.py` now holds only the pure,
      DB-free helpers (`ordered_legs`, `run_profit`, etc.) — shared unchanged by both the
      server and the client. `db/current_user.py` (the process-global from the direct-DB
      design) is deleted; ownership is resolved per-request from the JWT instead.
- [x] **`ledger_client.py` built — 2026-09-25.** The client's HTTP replacement for the
      old direct-DB calls — same public function names `trade_run_store.py`'s
      DB-touching functions used to have, so every tool file/`resolver.py`/overlay file
      only needed its import line split (pure calls stay on `trade_run_store`,
      DB-touching calls move to `ledger_client`), not a rewrite. Returns
      `ledger_schemas.TradeRunOut`/`TradeLegOut` (Pydantic, parsed from the server's
      JSON) rather than the SQLAlchemy classes — the pure helpers only ever do attribute
      access, so this is invisible to them.
- [x] **Auth rewritten for the relay — 2026-09-25.** `auth/discord_identity.py`'s old
      client-side PKCE-to-Discord flow (public client, no secret) is gone; it now opens
      `{ALICE_API_URL}/auth/discord/login` (the server) instead of Discord directly, and
      caches a JWT (`PilotSession`) instead of a raw Discord id. Re-validates the cached
      token against `/auth/users/me` before trusting it (`get_pilot_session`), so an
      expired token surfaces as one clean re-login instead of a confusing 401 from
      whichever tool happens to run first. `voice/__init__.py` updated to match —
      `thread_id` now keys off the pilot's username rather than a Discord id, since the
      client no longer holds one at all.
- [x] **Deployed live — 2026-09-25.** Root `Dockerfile` (copies the whole `app/` tree
      but installs only `server/requirements.txt` — no LangChain/Whisper/PySide6 in the
      image), `docker-compose.prod.yml` (Postgres with no published port; server bound
      to `127.0.0.1:8090`, nginx as the only path in), `scripts/deploy_server.sh`
      (modeled on `chicken-tracker/deploy-prod.sh`, same box/SSH key) — `/opt/graphling`
      on the shared Hetzner box, `api.heyalice.help` (DNS + certbot, discovered live that
      DNS has to resolve before certbot can issue a cert — `docs/deploy.md` now says so),
      Discord's Public Client off with a real secret registered against the server's own
      redirect URI. One deploy bug caught and fixed on the first real attempt:
      `docker-compose.prod.yml` had `image: graphling-server:latest`, missing the
      `feenstra32/` Docker Hub namespace, so `deploy_server.sh`'s tag-rewriting `sed`
      silently never matched and the server tried (and failed) to pull a bare image name
      that doesn't exist anywhere. Fully verified live end to end: `/health` over real
      HTTPS, and a real Discord login round trip through `casual_alacrity`'s account —
      the server's own exchange, `get_or_create_user`, JWT issuance (decoded and checked:
      `sub` is the local `users.id` UUID, not the Discord id), and the redirect back to
      the client's local listener all confirmed working against production.
- [x] **UEX/wiki cache migration gap found and closed — 2026-09-25.** The real end-to-end
      test above surfaced that the server move was incomplete: `tools/uexcorp/client.py`
      (`get_uex_cache`), `tools/route_ranking.py`, and `overlay/uex_lookup.py` (5 call
      sites) still hit Postgres directly for the UEX price/reference cache — it only
      "worked" in testing because the client's own `.env` still pointed `TRADE_DB_URL` at
      a local Postgres. Would have crashed Dennis's install outright on startup, since
      production Postgres has no published port at all. Fixed the same way the ledger
      was: `server/uex_cache_service.py` (thin `SessionLocal`-wrapping layer around the
      *same*, unchanged `tools/uexcorp/reference_cache_store.py`/`price_cache.py`
      functions — only who calls them moved) + `server/routes/uex_cache.py`, with a new
      client-side `uex_cache_client.py`. `UEXCorpClient.get_uex_cache()` keeps an
      in-memory L1 fast path but falls through to HTTP now, not Postgres;
      `UEXCorpClient.build_uex_cache()` (renamed from a private `_build_uex_cache`, since
      it's called from outside the class now) is the real UEX-API fetch the server uses
      on a cache miss.

      Also gave `tools/starcitizenwiki/client.py` (`StarCitizenWikiClient`) the same
      shared-cache shape, at the user's explicit request — it had no correctness bug (no
      Postgres dependency at all, pure in-memory per-process caching against a public
      API), but no cross-pilot sharing either. New `wiki_cache` table (kind/key/payload,
      mirroring `uex_price_cache`'s shape) + `server/wiki_cache_service.py` +
      `server/routes/wiki_cache.py` + `wiki_cache_client.py`; `fetch_ship_speed_from_wiki`/
      `fetch_locations_from_wiki` are the real-API-call methods the server uses on a miss,
      `get_ship_speed`/`get_locations` are the client-side HTTP-backed orchestration
      every existing caller keeps using unchanged.

      One real bug caught by writing the tests, not by inspection: `ShipSpeed`'s fields
      use `validation_alias=AliasPath(...)` to parse the wiki's nested API response, but
      `model_dump()`/FastAPI's response serialization always writes the flat field names
      — `model_validate()` on that flat JSON looks for the nested shape and always fails.
      Fixed on both ends (`wiki_cache_service.py`'s cache-hit path, `wiki_cache_client.py`'s
      response parsing) with `model_construct()` instead, which skips validation and just
      assigns — safe here since the data always came from a real `ShipSpeed`'s own
      `model_dump()`, never outside input.

      Extracted `api_client.py` (the authenticated-httpx-client boilerplate `ledger_
      client.py` already had) so `uex_cache_client.py`/`wiki_cache_client.py` share it
      instead of a third copy. Verified end to end locally with `TRADE_DB_URL` completely
      unset for the client process — `wiki_cache_client`, `uex_cache_client`, and
      critically `UEXCorpClient.get_uex_cache()` itself all still worked (1791 wiki
      locations, 205 UEX commodities, 826 terminals, real data through the local server).
      249 tests passing.

      **Deployed live — 2026-09-24.** One more bug found only by the real deploy, not
      local testing: `server/requirements.txt` was scoped too narrowly — assumed the
      server only needed the Postgres-touching parts of `tools/uexcorp/client.py`, but
      `uex_cache_service.py`'s cache-miss path calls straight through to that same
      class's other (`requests`-based) live-fetch methods as their fallback, and the
      file also imports `langsmith` unconditionally for `@traceable`. Container
      crash-looped (`ModuleNotFoundError`) until both were added — nginx's 502 correctly
      reported nothing was listening upstream. Fixed, verified booting locally before
      the second deploy, then confirmed live: both containers healthy, `wiki_cache`
      migrated correctly, `/health` green, `/wiki-cache/locations` reachable through
      nginx and correctly 401ing without a token.
- [x] **Post-patch cache refresh script — 2026-09-24.** The 24h TTL on the reference/wiki
      caches means a patch's changes (terminal/vehicle/ship data) could sit stale for up
      to a day waiting for it to expire naturally. `server/refresh_static_caches.py`
      (`docker compose exec server python -m server.refresh_static_caches`, see
      `docs/deploy.md`) force-rebuilds the UEX reference cache and re-fetches every ship
      name already in the wiki cache plus locations, bypassing the TTL check
      (`uex_cache_service.refresh_reference_cache`/`wiki_cache_service.refresh_ship_
      speed`/`refresh_locations`, new alongside the existing TTL-gated `get_*`
      functions they share fetch/store logic with). Deliberately excludes the UEX
      price/route cache — its 30-minute TTL already matches UEX's own price-data
      freshness window, so it self-heals long before a manual refresh would help, and
      the real patch risk (autoload flags, vehicle stats) is already covered by the
      reference cache rebuild alone, since `find_best_route` re-patches those against
      the *current* reference cache at query time regardless of what's cached in a
      stale route row. Verified live against real Postgres/UEX/wiki: 205 commodities,
      826 terminals, 282 vehicles, 1791 locations, all fetched_at timestamps confirmed
      fresh in the DB afterward. 256 tests passing.
- [ ] Multi-tenancy is also where the trade-data pipeline pools into — keep the schema
      open to per-observation station price/stock rows keyed by pilot + timestamp, even
      if that pipeline lands later.

Per-value **ledger provenance/confidence** is NOT part of this pass — shelved until
community features (leaderboards) need it, see Parked. If it's trivial to leave a hook,
fine, but don't design it now.
- [x] **Cache + reference tables stay global — enforced 2026-09-25**
      (`tests/db/test_tenancy_boundaries.py`). `UexPriceCache`/`UexReferenceCacheRecord`
      never got a `user_id`; a schema-column test now fails loudly if that ever changes,
      rather than relying on someone noticing during review. Shared economy data is the
      point — still true, and still enforced, under the server-mediated shape.
- [x] **Runs scoped to owner — done 2026-09-25.** `get_in_progress_runs`/
      `get_finalized_runs` filter by the JWT-resolved `user_id` server-side now, instead
      of a client-side process-global. No cross-visibility of active runs.
      **Scoping boundary, on purpose, unchanged by the server move:**
      `advance_leg`/`record_purchase`/`record_sale`/`finalize_run`/`delete_run` take an
      id directly and don't re-check ownership — a leg/run id is only ever learned via
      the two filtered list functions above, so this is scoped transitively. Revisit if
      ids ever start getting shared/logged cross-tenant.
- [x] Update CLAUDE.md's multi-tenancy framing — already done (it currently reads
      "Multi-tenancy is on the roadmap", no "v1 scope … no multi-tenancy" language left).

**Deferred (real, not now):** group / crewed runs; cross-pilot run visibility; the org
ops/leaderboard feature itself (only the schema hook for it lands now).

## Near-term — Cut Chainlit, collapse to one package

**Done 2026-09-11.** Mechanical, non-AI — Claude did it.

- [x] Delete `app/main.py`, `chainlit.md`, `app/chainlit.md`, `.chainlit/`,
      `app/.chainlit/`, `scripts/{mac,windows}/run-chainlit.*`.
- [x] `requirements.txt` — drop the `chainlit>=2.0` "Front end" section.
- [x] `pyproject.toml` — drop `main` from `py-modules`; rewrite `name`/`description`
      (no "Chainlit"); single entry point is `overlay.overlay_app:main` (already spawns
      voice); `voice:main` kept callable for quick voice-only debugging.
- [x] `overlay_app.py` — remove the `UPLINK_VOICE != "0"` conditional; voice always starts.
- [x] README — remove Chainlit sections, collapse the run-modes table to one line, drop
      `[voice path only]` caveats from the architecture diagram (it's the only path now).
- [x] CLAUDE.md — remove the Chainlit stack bullet; pair with the multi-tenancy scope edit.
- [x] Update lingering "alongside the Chainlit app" comments in `voice/`, `db/session.py`
      (the three-event-loop note loses one loop), `db/migrations/env.py`.
- [x] Leave the `TRADE_DB_URL` name as-is (was renamed off `DATABASE_URL` for a Chainlit
      collision — reverting is churn across `.env`/`alembic.ini`/`session.py` for no gain).

## Phase 3 — Tool-selection test harness (before the tool surface grows)

Goal: when Jeff swaps in a new model or a provider ships an update, run one suite and
see whether every tool still gets selected and called with the right args, and whether
outputs stay consistent.

- [x] **Deterministic external layer — built 2026-09-27.** Decided 2026-09-27: fake clients, not VCR-style
      cassettes — no new dependency, consistent with the rest of the suite's existing
      `httpx.MockTransport`/class-level-monkeypatch idiom, and easier to hand-craft the
      garbled/ambiguous entity-name edge cases (below) than to capture them live. Backed
      by one small shared "world" fixture (a fixed handful of terminals/commodities/
      ships/routes), a deliberate exception to the rest of the suite's one-fixture-per-file
      convention — the harness specifically needs the *same* world across many cases.
      The server-mediation migration narrowed what actually needs faking: tools reach
      UEX/wiki reference and route data through `uex_cache_client`/`wiki_cache_client`
      (HTTP to *our own* ledger server, not the live APIs) and trade-run state through
      `ledger_client` — only `UEXCorpClient`'s live per-request price/vehicle methods
      (`get_commodity_prices`, `get_vehicle_purchase_prices`, etc., called directly by 6
      of the 19 tools, uncached by design) ever touch a real external API from the
      harness's vantage point at all.
    - [x] **Live schema-drift check — framework built 2026-09-27, not yet scheduled.**
          The fake-client harness above only proves our parsing code agrees with itself;
          it can't catch UEX or the wiki actually renaming/dropping a field. Added
          `app/check_live_schema.py` — a standalone script (same shape as
          `server/refresh_static_caches.py`: run by hand or by an agent, not pytest-
          collected) that hits every real endpoint our client code ever calls
          (`build_uex_cache`'s 8 reference endpoints, `get_commodity_prices`,
          `get_terminal_prices`, `get_item_prices`, `get_commodity_routes`,
          `get_vehicle_purchase_prices`, `get_vehicle_rental_prices`,
          `fetch_ship_speed_from_wiki`, `fetch_locations_from_wiki`) against a handful of
          old, game-launch-era entities (Laranite, Cutlass Black, Freelancer) picked to
          not disappear on their own, and fails loudly — per-endpoint, with the actual
          exception — if a response no longer parses or comes back suspiciously empty.
          Verified both ways: a clean run passes every check against production UEX/wiki;
          a deliberately-broken bearer token correctly reports `SCHEMA DRIFT DETECTED`
          with the real `403` and exits 1.

          **Deliberately not scheduled yet** — decided 2026-09-27: we're still in active
          development, where daily use already surfaces a drift by hand faster than a
          weekly job would; this earns its keep once things are stable enough to go
          quiet (production, past a code-lock). Built now anyway so the eventual
          scheduling is just wiring, not design. When it does get scheduled (weekly,
          via this environment's own scheduled-cloud-agent feature): on drift, the agent
          **drafts a fix and opens a PR**, never auto-merges — same review bar as any
          other change to parsing code that feeds a live, paid-API-backed product.
    - [x] **`app/harness/` fake-client layer completed — 2026-09-27.** `world.py`
          (a small fixed set of commodities/terminals/a vehicle/a route row/wiki ship
          speed+locations, built from the real Pydantic models, not hand-rolled dicts)
          and `fakes.py`'s `install_fakes()` (class-level patches on `UEXCorpClient`'s
          live methods, module-level patches on `uex_cache_client`/`wiki_cache_client`)
          — proven end to end against `commodity_price_lookup` and
          `vehicle_purchase_lookup`, zero network calls. `ledger_client` (trade-run
          state) intentionally not faked yet — no dataset case needs it so far.
    - [x] **`evals/agent_eval/` — the LLM-in-the-loop harness itself, built and run for
          real 2026-09-27.** One eval run = one model, same convention `evals/
          start_trade_run_flow.py` already established (env-var driven, not an
          in-process multi-model API) — comparing models means running this twice and
          diffing the two report files. `cases.py`'s `HarnessCase` generalizes past
          pure tool-selection on purpose: `expected_tool: str | None` (`None` = no tool
          call is correct — covers a toolless reasoning/advice response, relevant once
          memory lands) plus `expected_on_topic: bool`, which directly answers this
          section's own open question above (item 4) about whether `classify_topic`
          misfires need a separate eval track — they don't, they're just another case
          shape in the same dataset. `judge.py` is a separate, fixed model (own env
          vars `HARNESS_JUDGE_PROVIDER`/`_MODEL`) grading only response *substance*
          against `expected_outcome` — tool name/args correctness stays a deterministic
          check in `run.py`, no LLM call needed for something code can verify exactly.
          `compare.py` diffs two report files (pass-rate/cost/token/latency deltas,
          per-case FIXED/REGRESSED/still-failing). Starter dataset: 4 cases x 3
          phrasings (commodity price, vehicle purchase, an off-topic decline, a
          toolless SC-knowledge question) — small on purpose, matching this section's
          own "hand-picked sample to start" framing.

          **First real benchmark run, against `ollama:gemma4` (warm), 2026-09-27:**
          10/12 passed (83%), $0 cost, 142,786 total tokens, ~29.7s avg latency
          (cold — `prewarm()` didn't fully absorb the first call's cost here). Caught
          two real issues, not synthetic ones: "What's the cheapest place to buy
          Laranite?" got **no tool call at all**, and "Can you explain what quantum
          drives do?" got a judged-incorrect explanation (missed the quantum-drive
          spool-up time, claimed near-instant repositioning). Report committed at
          `evals/agent_eval/results/ollama-gemma4__20260927-155736.json` — the
          checked-in baseline future runs get compared against. One practical note:
          the intended judge default (Anthropic Haiku) has only a placeholder key in
          this `.env` and OpenAI's configured key is out of credits, so this run
          used `HARNESS_JUDGE_PROVIDER=ollama HARNESS_JUDGE_MODEL=mistral-small` as a
          one-off override — the code's real default stays `openai`/`gpt-4o-mini`
          (documented in `judge.py`) once that account has credits again.

          **Local-model cost fixed 2026-09-28.** A model with no per-token API bill
          isn't free — `app/turn_metrics.py` now prices Ollama models by estimated
          system power draw (`LOCAL_MODEL_POWER_WATTS`, a spec-sheet guess — RTX
          4080 TDP + ~100W for the rest of the system — until FrankenLab is up and a
          real reading replaces it) x latency x Jeff's actual electricity rate
          (`ELECTRICITY_RATE_USD_PER_KWH=0.34`, not a guess), rather than the
          flat-$0 placeholder the first run above still carries.

          **Second data point, `qwen2.5:14b`, 2026-09-28:** 7/12 passed (58%),
          $0.043, ~91.2s avg latency — worse and slower than gemma4 on every axis,
          same judge both times. When it skipped the tool call (same Laranite miss
          gemma4 has), it didn't just fail silently — it hallucinated a wrong
          location/price from its own training data rather than the fake world's
          actual answer. Re-ran gemma4 too, under the corrected cost model, for a
          fair comparison: `evals/agent_eval/results/ollama-gemma4__20260928-074516.
          json` is the current baseline (supersedes the 2026-09-27 file, kept as
          history, not deleted — its cost numbers predate the electricity fix and
          aren't comparable to anything run since). That rerun scored 9/12 (75%),
          not the original 10/12, on a *different* case — real LLM sampling
          variance, not a bug. Worth remembering when reading any single comparison:
          a 1-2 case swing between two runs of the *same* model may just be noise;
          repeated runs / majority-vote scoring would settle this but isn't built.

          Still open from this section's older bullets, not yet built: evaluator (c)
          no-spurious-extra-tool-calls and (e) markdown/formatting-artifact checks;
          growing the dataset with the phonetic/garbled-entity and cross-turn-token
          cases (items 1/3 above) — `ledger_client` needs faking first for the
          cross-turn one.
- [ ] **Dataset of representative pilot utterances → expected tool call(s) + args.**
      Candidate phrasings per tool, plus uncovered gaps, in `docs/utterance-coverage.md`.
      Seed from real LangSmith traces plus tricky cases from git history (RMC matching,
      "is travel time included", cross-system routes, ambiguous ship names, compound
      "loaded it, what's the ETA"). Two concrete seed cases from designing `start_trade_run`
      (2026-09-11):
      1. `best_route` → `route_token` stashed → "Let's do it." → `start_trade_run` with
         *that exact token*, not just any token. First cross-turn dependency in the tool
         surface — the regression this guards is a model paraphrasing/dropping the token
         instead of copying it.
      2. The fuller `find_detour_pickup` script (best_route → start → shortfall detected →
         proactive offer → "Tell me." → "Add it." → second `start_trade_run` call) — a
         third dimension, a turn ALICE initiates rather than reacts to. Not runnable until
         `find_detour_pickup` and the graph-injection plumbing exist (Phase 4/backlog), but
         worth keeping as the target shape.
      3. Phonetic/garbled entity resolution must keep matching correctly. Real transcripts
         from the first live voice test (2026-09-21/22): Whisper rendered the ship "Railen"
         as "my railing" and, separately, as "Raylin'" in the same session, and the location
         "Orison" as "Oreson" — `best_route` resolved both correctly via the
         `token_sort_ratio` hedge match (see the comment at `best_route_tool.py`'s
         `_resolve_origin`). A good positive snapshot case — a future model swap regressing
         this fails silently otherwise, since a *confident wrong* match (see the "Orson" →
         "HDMS-Ander(son)" near-miss already documented there) looks identical to a correct
         one without a known-good answer to check against.
      4. `classify_topic` misclassifying a benign or in-progress turn as off-topic. Two real
         instances: "All. Good morning." (live trace, 2026-09-22) got declined with the
         canned "I don't do small talk" line, and "Let's do it." after a failed search was
         declined the same way (`classify-topic-rework.md`'s open questions) — the
         classifier has no signal for pending-confirmation state. **Resolved 2026-09-27:**
         doesn't need a separate eval track — `evals/agent_eval/cases.py`'s
         `expected_on_topic` field covers it as one more case shape in the same dataset
         (an `off_topic_greeting` case reproducing the "Good morning" pattern is already
         in there). The "Let's do it." pending-confirmation variant isn't a dataset case
         yet — same shape, just needs adding.
      5. **Inference/ambiguity-gating class of case (2026-09-23).** "I sold the copper for
         4,567 per SCU" said directly, with arrival/unloading never separately confirmed —
         does the model pick `mark_cargo_sold` with the *exact* stated price, not a
         paraphrase or a fallback to the leg's planned price? The backfill itself
         (`catch_up_before_transaction`) is ordinary deterministic code now, covered by
         unit tests, not something the harness needs to check — but "did it call the right
         tool with the right args from an inference-shaped utterance" is exactly evaluators
         (a)/(b) above, and this is the general *shape* worth growing more cases in: any
         utterance where the correct tool call requires recognizing what a stated fact
         necessarily implies, not just extracting an entity from it. Decided 2026-09-23:
         push whatever can be made deterministic into code rather than leaning on the
         model to plan multi-step chains — the harness is for judgment calls (tool/arg
         selection), not a substitute for removing judgment from things that don't need
         it. A hand-picked sample of phrasings is fine to start from and grow over time —
         the point is catching inference issues with structure, before they're anecdotal
         reports from actual pilots.
- [ ] **Evaluators:** (a) correct tool selected — **done**, deterministic check in
      `run.py`. (b) args resolved to the right entities — **done** for exact/case-
      insensitive match; not yet fuzzy. (c) no spurious extra tool calls — **not
      built**, `run.py` only checks the first tool call today. (d) output snapshot /
      consistency check so drift between models is visible — **done**, `compare.py`.
      (e) no markdown or other formatting artifacts in any spoken response, and a
      tool's own spoken-form phrasing (e.g. "960 thousand aUEC") is relayed as given,
      not recomputed or reformatted back to raw digits — **not built**; the judge
      grades substance, not formatting, today. Regression measured live 2026-09-22:
      `best_route`'s phrased profit figure came back as `960,000 aUEC` inside a
      markdown bullet list in the final persona reply.
- [x] **Runner + report — built 2026-09-27.** `evals/agent_eval/run.py` +
      `compare.py`, JSON report files checked into `evals/agent_eval/results/`, not
      LangSmith `evaluate()` — see the harness build entry above for the full design.
- [x] **Track per-case latency alongside correctness — built 2026-09-27.**
      `app/turn_metrics.py`'s `TurnMetrics.latency_ms`, wall-clock around the whole
      `graph.ainvoke()` call, recorded per case in the report file plus averaged in
      its summary. Concrete baseline from 2026-09-22 (Gemma 4, local, no FrankenLab
      yet): a session's first `respond` call cost 29.64s of prompt-eval alone for
      a ~9,700-token prompt (persona + all 18 bound tool schemas, before any real
      conversation) — later calls in the same session reused the cache and dropped to
      ~0.23s, and steady-state generation held ~28 tokens/sec regardless. Both numbers are
      levers this harness should keep visible: tool-schema bulk (bind fewer/smaller tools,
      or delegate to `agent-roles.md`'s datarunner) and raw hardware throughput.
- [ ] **Voice stage timing — whisper → graph stages → TTS → playback, per live turn**
      (Claude — instrumentation only). Spec: `docs/voice-stage-timing.md`. First step of
      `docs/intent/voice-latency.md`; measured against the latency tiers in
      `docs/utterance-coverage.md`. `latency_ms` above is graph-only, a lower bound.

- [ ] **Travel-time model covers only horizontal distance.** Measured 2026-09-18: Orison
      TDD to Admin - Seraphim, a climb out of atmosphere, estimates at 0.1 min because
      both share the Crusader orbit. Missing surface->orbit, orbit->surface, jump-gate
      transit, and the hydrogen burn from orbit to hangar. This matters more than it
      looks: profit/hour is the ranking criterion for every recommendation, so a
      systematic undercount on one kind of flight biases every route ALICE picks toward
      orbital-to-orbital runs. Also blocks scoring an approach leg. Full write-up and
      what already exists in `docs/intent/travel-time-model.md`. Numbers are measurable
      in-game — worth timing a few legs during an op night.

- [ ] **Route search ignores the buy-in.** `get_commodity_routes` accepts an `investment`
      parameter and `find_best_route` never passes it, so ALICE can recommend a run the
      pilot can't afford to fill. Not currently felt — Jeff trades with ~15M aUEC and has
      never failed to fill a Railen — but it's fiction for a pilot who's just started or
      just lost a ship, and per-run profit is the number they'd be judging it by.

## Phase 4 — Grow tools / functionality

SC-focused, but each addition weighed against "would this also serve assistant-ALICE
someday?" Timers already pass that test and stay. Candidates to be brainstormed
separately — don't pre-commit a list here.

- [ ] **Confirmed-voice LEG finalize — future todo, reconfirmed 2026-09-24.**
      Finalize stays manual-only for now; explicitly revisiting later, not deciding for
      or against it today. Scope, if it ever happens: **leg** finalize only, never run
      finalize — locking a run into the ledger always needs the pilot's own review and
      click, full stop, no exception. In practice this can only ever fire for the
      **acquisition** leg — it's the only leg with a next leg in-run that could be
      blocked behind it; nothing blocks after the sale leg except the run's own finalize,
      which this never touches.

      A worked scenario ("I sold the copper for 4,567/SCU" while the acquisition leg is
      still unfinalized) surfaced two real gaps. Both are now resolved, independent of
      whether voice-finalize itself ever gets built:
      1. **[x] `resolver.AmbiguousLegError` was broken — fixed 2026-09-23 (`085c2ad`).**
         It never called `super().__init__()`, so `str(error)` — what every milestone
         tool returned verbatim to the persona — was Python's raw repr of the candidate
         list. Verified live: `"[<db.models.TradeLeg object at 0x100a41a60>, ...]"`.
         Both `AmbiguousLegError` and `AmbiguousRunError` now build a real message
         naming ship/commodity/terminal per candidate; audited all 6 catch sites, not
         just the one that got noticed, and caught two more bugs in `trade_advisor_tool`
         along the way (unused `ship` arg, a misleading fallback message).
      2. **[x] The "infer arrival + unloading from 'I sold it'" conflict with the
         persona's "don't chain tool calls" rule — resolved 2026-09-23, decided B, not A.**
         Rather than have the LLM call `mark_arrived` → `confirm_cargo_unloaded` →
         `mark_cargo_sold` itself (which would need the rule loosened, and is exactly the
         kind of multi-step plan a small local model is least reliable at), the entailed
         backfill now happens *inside* `mark_cargo_sold`/`mark_cargo_acquired` themselves
         (`trade_run_store.catch_up_before_transaction`) — one tool call in, one out. The
         persona's "don't chain" rule never needed to change: there's no LLM-orchestrated
         chaining happening, so it still correctly blocks what it was written to block.
         Covered by unit tests (`test_trade_run_store.py`, `test_mark_cargo_acquired_
         tool.py`, `test_mark_cargo_sold_tool.py`) — deterministic, not dependent on model
         behavior. Logged as a Phase 3 harness seed case above (item 5) for the part that
         *does* still need a model: picking the right tool with the right stated value.
         Still overlaps conceptually with the parked **Force-complete path** idea
         (`docs/ledger-trust-and-corrections.md`) if that ever gets built for real.

      **Still open, and still separate from the above:** this entry's own subject — ALICE
      finalizing the acquisition leg on voice confirmation to unblock the sale leg *at
      all* — is unaffected by either fix. The backfill above only ever helps once a leg
      is already reachable; a sale leg blocked behind a genuinely unfinalized acquisition
      leg still can't be resolved by voice at all today, same as before. That's still
      just a flagged pain point, not a decision.
- [ ] **User setting to bypass the confirm-before-finalize guard entirely** — an
      intentional pilot opt-in once there's a real client + user-settings surface
      (multi-tenancy, Phase 2+). Not buildable until that infra exists; flagged here so
      it isn't lost.

Named so far (from `docs/ledger-trust-and-corrections.md`, HCI design session) — the
*simple* versions; the provenance/confidence layer they were originally coupled to is
Parked:
- [ ] **`correct_trade_value`** — voice-driven amendment of a recorded value pre-finalize,
      re-derives profit. Just updates the value; no `source`/`confidence` tagging for now.
- [ ] **Force-complete path** — proceed past a data-blocked milestone with expected
      values (soft constraint / graceful degradation), plus the "ask, never accuse"
      phrasing for a missing prerequisite. A plain "estimated" flag is enough; skip the
      full trust model.
- [ ] Verify + test **multi-milestone single utterance** ("landed and unloaded" → 2
      transitions) — good Phase 3 harness case.
- [ ] **Return-trip default on `best_route`** — infer `origin` from the active run's
      destination when the pilot says "here"/"for the way back", same pattern as `ship`
      already falling back to the active run. Free, no new tool.
- [ ] **`find_detour_pickup`** — the SCU-shortfall "second pickup stop" case, corrected
      shape (candidate terminal near the *original* acquisition origin, three-point
      travel time, detour-vs-partial-fill scoring). See `trade-route-tracker.md`'s
      SCU-shortfall section. Genuinely new engineering, not a `best_route` parameter.
      Trigger/gating/voice details sketched there too (2026-09-11) — notably needs
      graph-injection plumbing for a proactively-initiated turn, which nothing today has.
- [ ] **Ledger-inferred defaults on `best_route`.** Infer ship/origin/constraints
      (autoload, space-only) from recent finalized runs when the pilot doesn't name them
      — "last 3 runs were a Railen from Orison, autoload/space-only." Ask, don't guess, on
      conflict or ambiguity. Extends the already-designed-but-unconsumed "query-time
      parameters" idea in `trade-route-tracker.md` to `best_route` itself, not just
      Trade Advisor. `get_finalized_runs` already exists; needs the aggregation + wiring.
- [ ] **Hangar-size / Hull-C landing constraints** — backlogged, see
      `trade-route-tracker.md`'s "still open / not yet scoped" list.
- [ ] **Tool output design — flagged 2026-09-24, not broken, revisit later.** Two related
      but distinct concerns from a design discussion, neither urgent enough to act on now:
      1. **Trade-run action tools' hand-composed phrase combinations.** `mark_cargo_sold_
         tool.py` hardcodes three "recorded X, Y, and the sale" phrasings in Python because
         a generic join read badly — arguably doing a little of the persona's job. Possible
         fix, *not* a wholesale switch to UEX-tool-style structured data: keep success/
         failure as an unambiguous field (never something inferred from a composed
         sentence's tone — Section 6 of the persona prompt explicitly leans on tools
         self-reporting outcomes plainly, and that's a real safety property worth keeping
         for ledger-mutating tools), but let the *caught-up narrative* specifically
         (`caught_up=["arrival", "unloading"]`) be structured data the persona phrases,
         rather than a hardcoded lookup table of every combination.
      2. **`best_route_tool.py`'s `_format_reply` smuggles route tokens inside the spoken
         text itself** — `f" (Internal note, don't say this part aloud: {tokens_note}.)"`
         — relying on the model reliably recognizing and obeying that parenthetical every
         single reply, forever. The idiomatic LangChain fix is a tool `response_format=
         "content_and_artifact"` return: `content` is the spoken message, `artifact` is
         the route token(s), never entering the model's context as text to (mis)read
         aloud at all. Worth checking whether `trade_advisor_tool.py` has the same
         pattern when this gets picked up — not currently, per a live grep.

## Phase 5 — Tool-selection improvements (informed by Phase 3)

- [ ] Baseline current tool-selection accuracy with the harness.
- [ ] Address what the harness surfaces — tighten descriptions, group/namespace tool
      families, few-shot in the persona, or a router node. Pick based on where failures
      actually are.
- [ ] Revisit whether the binary `classify_topic` guardrail needs to change.

## Presence layer (UI track — runs alongside the phases)

Full design: `docs/presence-layer.md`. Two surfaces kept separate — the workbench (the
existing F3 overlay) and the presence layer (mini tracker + transient cards). Tools emit
structured display intents to a queue the Qt layer renders; nothing parses ALICE's prose.

- [ ] **Tier 1 — polish what exists.** Builds `app/overlay/animations.py`. Open/close
      transition, tab crossfade, milestone-advance flow, results fade-in, leg progress.
      Independent — can start anytime.
- [ ] **Tier 2 — presence layer foundation.** Display-intent event bus + the mini tracker
      (current leg / next step, strike-through-and-fade on advance). Wire `mark_*` tools
      to push updates. After Phase 1.
- [ ] **Tier 3 — transient cards.** Timer countdown; route suggestion (workbench open →
      pin to top of results; closed → non-modal card). After Tier 2.
- [ ] **Tier 4 — onboarding.** Step text carries a "say this" voice hint; verbose
      first-run mode that dials back. After Tier 3.
- [ ] **Tier 5 — group ops.** Invite card / terms / accept / reward settle. Needs
      Phase 2 + Tier 2-3 + cross-client events. Later.

## Phase 6 — Star Citizen content polish pass

- [x] **Naming — user-facing surface standardized on ALICE, 2026-09-18.** Package renamed
      `uplink` → `alice`; entry points are now `alice` (the one supported command) and
      `alice-voice` (debug); run scripts consolidated to `run-alice.sh`/`.bat` with the
      voice-only variants deleted; voice-loop prints and the TTS docstring no longer label
      ALICE's own replies as Uplink. `UplinkTool`/`UEXBackedTool` deliberately kept — Uplink
      is the data layer, not a stray name. Full reasoning in `docs/intent/agent-roles.md`.
      **Note for existing installs:** the rename leaves a stale `uplink` distribution and
      its old console scripts behind; `pip uninstall uplink` after re-running setup.
- [x] **Land the uncommitted batch.** Landed since this was written — verified: mac/windows
      run scripts exist, README has no Chainlit references, `.gitignore` carries the
      prompt-cache rationale, singleton-listener fix is in git history (`8385023`).
- [ ] **README accuracy.** Tool count verified at 17 (6 UEX + 7 trade-run + 4 general).
      Demo clip still a placeholder.
- [ ] **`trade-route-tracker.md` cleanup.** Fold dated "Known gaps" / live-testing notes
      into current state once Phase 1 lands. Consider trimming process-y sections before
      the repo goes portfolio-facing.

## Parked

- **SC-vs-general router / mode-split.** The eventual companion-ALICE-that-moonlights
  architecture. Not scoped now. New tools should generalize where cheap, so this is
  easier later; don't build the routing itself.
- `pilot-preference-memory` branch — generic memory system, stays shelved. Note:
  assistant-ALICE *would* give memory the concrete "reason to need it" the trade
  optimizer never had — revisit if/when that direction goes live.
- **Per-value ledger provenance / confidence / run trust-score** (`docs/ledger-trust-and-`
  `corrections.md` §1). Designed, deliberately shelved 2026-09-10 — "right now it's not
  going to help the pilot or ALICE." Comes back with community features (leaderboards for
  most traded / most earned / contribution), where per-pilot per-value provenance enables
  fair, auditable standings. Likely wants its own migration when it lands.
- Always-on overlay auto-popup finalize dialog — after the current UI approach.
- Post-sale market observation / pseudo-datarunner cache — folded into the trade-data
  pipeline phase (see top of doc).
- Group/crewed runs, cross-pilot visibility, org ops/leaderboards — future; only the
  `user_id` schema hook lands now.
