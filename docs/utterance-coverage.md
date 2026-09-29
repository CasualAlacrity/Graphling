# Utterance coverage

What a pilot would plausibly say to ALICE, mapped against what she can actually handle —
written 2026-09-29 against the 19 tools bound in `app/graph.py`.

Two jobs:

1. **Covered** — phrasings to grow `evals/agent_eval/cases.py` with. The current 3 cases
   only exercise clean, single-intent, well-formed questions; live pilots speak in
   fragments, lean on context, compound requests, and get garbled by Whisper.
2. **Gaps** — what a pilot would say that nothing handles: open (ranked), parked behind
   an external dependency, or deliberately not closed.

Plus a short list of gaps **deliberately left open**, so they aren't re-proposed.

Sources for the outside view: [Wingman AI](https://www.wingman-ai.com/) (voice → keypress
macros, ATC, generic data skills) and [SC-LOADMASTER](https://apps.apple.com/ru/app/sc-loadmaster/id6759680432)
(hauling-contract planner: manifest, container breakdown, run log, fleet registry).

---

## Latency tiers — the guideline

Decided 2026-09-29. One latency number can't cover "where can I rent a Hull B?" and "find
me the best route" — every utterance class gets its own budget. Measured end to end: **PTT
release → Whisper → graph → first ElevenLabs audio playing**, not just `graph.ainvoke()`.
The *why* and the plan to hit these live in `docs/intent/voice-latency.md`.

Two clocks per utterance:

- **First audio** — time until ALICE says *anything*. A pre-recorded acknowledgement counts.
- **Answer** — time until she says the thing the pilot actually asked for.

| Tier | What | Examples | First audio | Answer | Feedback |
|---|---|---|---|---|---|
| 0 | No model needed | "Say that again" · "Stop" · "Never mind" | < 1s | same | none |
| 1 | Single lookup | "Where can I rent a Hull B?" · prices · run status · timers | ≤ 3s | same | none — the answer *is* the first audio |
| 2 | Ledger write | "Sold it all, 4,567 a unit" · "Landed" · "Loaded" | ≤ 3s | same | none — a short confirmation |
| 3 | Heavy compute | `best_route` · `trade_advisor` · compound requests | ≤ 2s (ack) | ≤ 10s | ack, then stage updates |

Rules that go with the table:

- **Tier follows the tool.** A case's tier is derived from its expected tool, so the
  harness can fail a case for blowing its budget, not only for picking the wrong tool.
  `harness latency_ms` today times `graph.ainvoke()` alone — a lower bound, not the budget.
- **The numbers are starting targets, not truths.** Revise them from measured per-stage
  timings once those exist; don't defend a number the data contradicts.
- **Stage updates must be true.** Tier-3 progress lines report real stages ("Checking prices
  out of Orison…", "Ranking forty routes…"), never timed filler like "almost done" that
  turns out not to be. A progress line that lies trains pilots to tune ALICE out.
- **Stage updates are toggleable** — by us per tool (on only where measured averages say a
  pilot would otherwise sit in silence) and by the pilot (some won't want to hear them).
  Acknowledgements and final answers are not optional.

---

## Covered — phrasings to add as eval cases

| Area | Tool | Harder phrasings |
|---|---|---|
| Commodity prices | `commodity_price_lookup` | "Who's paying the most for quant right now?" · "Is Laranite worth taking to Hurston?" · "What about in Pyro?" (follow-up — should reuse the prior result, not re-call) · "RMC at Grim Hex" (salvage slang) |
| Items | `item_price_lookup` | "Where do I get a Pyro RYT multitool?" · "Cheapest size 3 quantum drive" · "Can I sell my old FS-9 anywhere?" |
| Vehicle purchase | `vehicle_purchase_lookup` | "How much is a Hull A in-game?" (must not mention pledge prices or resale) |
| Vehicle rental | `vehicle_rental_lookup` | "Can I rent a Cat for a day near Lorville?" |
| Mining | `mining_location_lookup` | "Where's Quantainium on Daymar?" · "Hadanite spots in Stanton" |
| Refining | `refinery_yield_lookup` | "Best refinery for Gold near me" |
| Route finding | `best_route` | "I've got 96 SCU and 400k, what should I haul?" · "Something short, I've got 20 minutes" · "Nothing in Pyro" · Whisper-garbled names ("my railing" → Railen, "Oreson" → Orison — todo Phase 3 seed #3) |
| Commit a route | `start_trade_run` | "Yeah, do it" · "Start it but only 500 SCU" · "Let's do the second one" (runner-up token) |
| Arrival | `mark_arrived` | "Landed." · "I'm at the buyer." |
| Buy | `mark_cargo_acquired` | "Bought 640 at 14.2" · "Got the copper" (no numbers — falls back to plan) |
| Load / unload | `confirm_cargo_loaded` / `_unloaded` | "Loaded, what's the ETA?" (compound — two tools, in order) · "Cargo's off" |
| Sell | `mark_cargo_sold` | "Sold it all, 4,567 a unit" (exact stated price — todo seed #5) |
| Run status | `trade_run_status` | "Where am I going again?" · "What's after this?" · "How much am I carrying?" |
| Reassess a run | `trade_advisor` | "Is it still worth going to Pyro?" · "Did I pick badly?" |
| Container mix | `cargo_packing_suggestion` | "What crates do I buy?" · "Can I fit 32s in the Railen?" |
| Travel time | `estimate_travel_time` | "How far to Baijini?" · "Can I make it to Pyro?" (cross-system — must say so plainly) |
| Timers | `start_timer` / `check_timer` | "Loading takes 15, ping me" · "Refinery job's 4 hours, remind me" · "How long left?" |
| Topic gate | `classify_topic` | "Let's do it." after a failed search (pending-confirmation — todo seed #4, still not a case) · "Morning ALICE, what's good to haul?" (greeting + real request — must be on-topic) |

Most of these need matching fixtures in `app/harness/world.py` before they can run —
the dataset is only reproducible against the fixed world.

### Behaviour cases worth adding regardless of tool

- **Decision-seeking:** "Just pick one for me." / "Which should I do?" — per the
  information-not-decisions principle, the correct response presents options and does not
  choose. Guards against a model swap quietly starting to decide for the pilot.
- **Unknowable:** "Is Pyro safe right now?" — no data source exists; the correct response
  says so rather than improvising. `expected_tool=None`.

---

## Partial — a tool exists, but these utterances break it

- **Corrections** — "No, it was 620, not 640." · "Actually I sold at Area18." No tool
  edits a recorded leg — and neither does the overlay (checked 2026-09-29): once a
  purchase/sale is recorded, the server refuses a second record, no update endpoint
  exists, the Mark Done and Finalize Run views are read-only recaps, and Abandon
  disappears once anything is bought. A misheard voice purchase is currently permanent. Designed in `ledger-trust-and-corrections.md`, not built. Pilots
  will say this constantly, especially after Whisper mishears a number.
- **Abandon / loss** — "Scrap this run." · "I died, cargo's gone." · "Got pirated." No
  terminal state for a failed run. Also valuable data later (route risk).
- **Setting the ship** — "I'm in the C2 today." Ship only arrives via `best_route`, so
  ship-dependent follow-ups (packing, travel time) fail otherwise.
- **Departure** — "Leaving now." · "Quantuming out." Only arrival is stamped, so in-flight
  ETA / leg timing can't be tracked.
- **Partial sale** — "Sold 300, keeping the rest for Area18." Likely falls back to planned
  quantity or errors.
- **Travel time off the horizontal plane** — tool is picked correctly, but surface→orbit,
  atmosphere climbs, and jump gates are undercounted (todo: travel-time model). Every
  "how long to Orison" from a surface is wrong, and that biases profit/hour ranking.

---

## Gaps — open, ranked

1. **Ledger history / earnings** — "How much did I make today?" · "Best route this week?"
   · "Average profit per hour on Laranite?" · "What was my last run?" The data is already
   in Postgres; this is a read tool over it. Also the first thing that makes ALICE feel
   like she knows *this* pilot — the reason generic memory was shelved.
2. **Ship facts** — "How much SCU does a Hull C hold?" · "C2's quantum range?" · "Can my
   Freelancer land at Orison?" Currently falls to the toolless path, where the model will
   confidently invent specs — worse than no answer. The wiki client exists; nothing
   exposes it for this. (Same reason `cases.py` excludes general-knowledge cases.)
3. **ALICE meta-commands** — "What can you do?" · "Say that again." · "Shorter." ·
   "Never mind." · "Stop." Cheap, and failures here damage the persona out of proportion.
   "Repeat that" is table stakes in a voice-only interface.
4. **Mining / salvage economics** — "Is this rock worth it? 12% Quant, 8,000 mass." ·
   "Refine or sell raw?"

---

## Parked — blocked on something outside ALICE

Decided 2026-09-29. Wanted, but not buildable well until the dependency exists.

- **Hauling contracts** — "3-drop contract, plan the order." · "Can I fit these two
  contracts in one trip?" Core SC gameplay (SC-LOADMASTER's whole product), but very
  difficult: contract offers aren't in any API, and doing it properly leans heavily on
  OCR. Other players have built tools here — check those before designing anything.
  **Blocked on:** OCR (`intent/ocr-trade-data.md`).
- **Fleet / hangar** — "I own a C2 and a Cutlass, which fits this?" · "Where's my Railen
  parked?" Hoping a third party (CitizenID, CCU Game, or similar) auto-pulls a player's
  hangar and exposes it as an API, rather than ALICE maintaining its own fleet list.
  **Blocked on:** an external hangar API existing.
- **Risk / conditions** — "Any pirates at Jumptown?" · "Is Pyro safe right now?" Needs a
  live community signal — effectively a community ham radio — that doesn't exist. Until
  then the correct behaviour is an honest "I don't know" (behaviour case above), so the
  eval case still applies. **Blocked on:** a community reporting channel.

---

## Deliberately not closing

Decided 2026-09-29 — don't re-propose these.

- **Ship control / keypress macros** ("lower gear", "request landing", ATC). A gimmick,
  and Wingman AI's established free turf. Competing there dilutes the trade-data thesis.
- **Loadout / build optimization.** Erkul owns it; pulls ALICE away from trading.
- **Free-floating pilot-reported prices** ("Copper's selling for 7.2 here", "terminal's
  out of Laranite") as a voice-input feature. Unverifiable claims make the pool
  untrustworthy very fast. Freshness is the moat, and a pool nobody trusts has none.
  Not the same as the prices `mark_cargo_acquired`/`mark_cargo_sold` record: those land
  in the pilot's *personal* ledger, where a Whisper mishearing costs only that pilot and
  is seen at the manual leg/run Finalize review — though as of 2026-09-29 it can't yet be
  *corrected* there (recorded transactions are read-only; see "Partial → Corrections"). A
  free-floating report would go straight into shared state with no review gate, where a
  mishearing misdirects every pilot reading the cache.
  **Rule (2026-09-29):** the only data passed to other players or upstream data brokers
  is ALICE VLM-captured content. Anything a pilot enters, by voice or by hand and
  finalized or not, stays in their own ledger.
- **ALICE replacing the pilot** ("just run it for me", auto-play). Violates
  information-not-decisions. The decision-seeking behaviour case above exists to keep this
  from creeping in through a model swap.
