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
    - [ ] Tests still needed — see `docs/start-route-tool.md` "Tests still to add".
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
      `delete_run`. Plausibly intentional (same "consequential action, manual-only"
      logic as `finalize`), but that's implicit, not a decision anyone's actually made.
      Worth a one-line call: leave voice-inaccessible on purpose, or add later as a
      confirmed-only action.

## Phase 2 — Multi-tenancy (shared backend, per-user client)

Shape: one shared Postgres both pilots reach; each runs their own voice-loop client
tagged with a user id. Individual co-pilot only — not fleet logistics. CLAUDE.md already
frames this as an additive migration, not a redesign.

- [ ] **`user_id` on `TradeRun` / `TradeLeg`.** FK (or plain string id for now), backfill
      a default tenant, Alembic migration. All trade-ledger reads/writes filter by it.
      Get this right now so future per-pilot aggregation (org ops / leaderboards — most
      hauled, greatest value, contribution) is a query, not a migration under pressure.
- [ ] Multi-tenancy is also where the trade-data pipeline pools into — keep the schema
      open to per-observation station price/stock rows keyed by pilot + timestamp, even
      if that pipeline lands later.

Per-value **ledger provenance/confidence** is NOT part of this pass — shelved until
community features (leaderboards) need it, see Parked. If it's trivial to leave a hook,
fine, but don't design it now.
- [ ] **Cache + reference tables stay global.** `UexPriceCache`,
      `UexReferenceCacheRecord` — no tenant column. Shared economy data is the point.
- [ ] **Client carries identity.** `ALICE_USER` (or similar) in `.env` per install;
      `thread_id` becomes `f"{user}:{uuid}"`. `MemorySaver` is in-process so separate
      client processes are already isolated — only move to `AsyncPostgresSaver` if
      checkpoint persistence or shared threads are wanted.
- [ ] **Infra:** Postgres moves from each person's local `docker-compose` to one
      always-on reachable host (small VPS, or Tailscale to one machine). `db/session.py`
      NullPool setup is already multi-loop-safe; multi-process is fine.
- [ ] **Runs scoped to owner.** No cross-visibility of active runs for now.
- [ ] Update CLAUDE.md's "v1 scope … no multi-tenancy" section to match.

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

- [ ] **Deterministic external layer.** UEX Corp + Star Citizen Wiki HTTP calls recorded/
      replayed (VCR-style cassettes) or behind a fake client, so the suite is stable and
      offline. The UEX cache layer may already give a seam.
- [ ] **Dataset of representative pilot utterances → expected tool call(s) + args.**
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
         classifier has no signal for pending-confirmation state. Not a tool-call case, so
         it may need its own small eval track rather than living in this dataset — noting it
         here so it isn't lost before that decision gets made.
- [ ] **Evaluators:** (a) correct tool selected, (b) args resolved to the right
      entities, (c) no spurious extra tool calls, (d) output snapshot / consistency
      check so drift between models is visible, (e) no markdown or other formatting
      artifacts in any spoken response, and a tool's own spoken-form phrasing (e.g. "960
      thousand aUEC") is relayed as given, not recomputed or reformatted back to raw digits
      — regression measured live 2026-09-22: `best_route`'s phrased profit figure came back
      as `960,000 aUEC` inside a markdown bullet list in the final persona reply.
- [ ] **Runner + report.** Executes the suite against a named model via `get_chat_llm`
      and produces a pass / consistency summary. Decide: LangSmith `evaluate()` over a
      Hub dataset, local pytest, or both.
- [ ] **Track per-case latency alongside correctness**, not just pass/fail — this harness is
      also where a model swap or FrankenLab coming online gets measured, not just whether
      tool selection held up. Concrete baseline from 2026-09-22 (Gemma 4, local, no
      FrankenLab yet): a session's first `respond` call cost 29.64s of prompt-eval alone for
      a ~9,700-token prompt (persona + all 18 bound tool schemas, before any real
      conversation) — later calls in the same session reused the cache and dropped to
      ~0.23s, and steady-state generation held ~28 tokens/sec regardless. Both numbers are
      levers this harness should keep visible: tool-schema bulk (bind fewer/smaller tools,
      or delegate to `agent-roles.md`'s datarunner) and raw hardware throughput.

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

- [ ] **Confirmed-voice finalize (2026-09-23 decision).** Reverses the locked
      manual-only-finalize decision on purpose: if the pilot tries to act on a leg that's
      blocked behind an unfinalized predecessor, ALICE says so and offers to finalize it
      ("The first leg hasn't been finalized. Want me to do that?"); on "yes" she may. The
      review-friction the manual-only rule existed for is intentionally traded for less
      friction here — named explicitly, not walked into. Needs: a `resolve_leg` fix so
      "blocked by predecessor" is distinguishable from "no such leg" (fixes every
      milestone tool at once, they all funnel through it); a narrowly-scoped new
      `finalize_leg` tool (the store-level `advance_leg` already supports this — the
      restriction was only ever at the tool layer); a decision on whether ALICE
      auto-retries the originally-blocked action after finalizing or makes the pilot
      repeat themselves (leaning: auto-retry); two persona-prompt additions (the "why
      isn't this automatic" explanation, and the auto-retry instruction).
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
