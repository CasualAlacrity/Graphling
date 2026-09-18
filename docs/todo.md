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
    - [ ] Docs said "done" but this hasn't been run live yet — first real voice test of
          best_route → start_trade_run → mark_arrived → ... → finalize is still ahead.
    - [x] **Unselectable runner-up fixed 2026-09-18.** `best_route` named a second route
          but stashed only the winner, so "do that one instead" had nothing to resolve to;
          `find_best_route` also never returned the runner-up's SCU, so it couldn't be
          committed even with a token. Both fixed — see `docs/start-route-tool.md`.
          Standing rule that came out of it: any route ALICE mentions aloud must be
          selectable.
- [ ] **Decide Trade Advisor's fate.** `app/tools/trade_run/trade_advisor_tool.py` is
      built but parked out of `graph.py`'s tool list (commit `9f246c4`). Re-add as-is
      (cheap, it works) — recommended — vs. leave parked vs. build the fuller
      ledger-benchmarked version (needs real run data, so not now).
- [ ] **Overlay ↔ AI shared-state check.** Confirm a run the AI creates or advances by
      voice shows up live in the overlay's trade-runs panel (Qt refresh / threading).
      Claude can do this.
- [ ] **Manual/AI parity pass.** Every milestone reachable by voice has a manual
      equivalent and vice versa. `finalize` (leg and run) stays manual-only — locked
      decision, memory `project-trade-run-finalize-checkpoints`. AI may *prompt* the
      pilot to finalize, never do it.

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
- [ ] **Evaluators:** (a) correct tool selected, (b) args resolved to the right
      entities, (c) no spurious extra tool calls, (d) output snapshot / consistency
      check so drift between models is visible.
- [ ] **Runner + report.** Executes the suite against a named model via `get_chat_llm`
      and produces a pass / consistency summary. Decide: LangSmith `evaluate()` over a
      Hub dataset, local pytest, or both.

## Phase 4 — Grow tools / functionality

SC-focused, but each addition weighed against "would this also serve assistant-ALICE
someday?" Timers already pass that test and stay. Candidates to be brainstormed
separately — don't pre-commit a list here.

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
- [ ] **Land the uncommitted batch.** mac run scripts, new README, voice
      singleton-listener fix, `.gitignore` prompt-cache rationale, Windows path fixes,
      deletion of `ai-presentation-narrative.md` + `Vector Tracking Ideas.md`. Group into
      clean commits. (Fold the Chainlit removal in here — see "Near-term" above.)
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
