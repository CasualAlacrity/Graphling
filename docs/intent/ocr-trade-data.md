# Intent — crowd-sourced trade data capture (Auspex)

**Status:** not started, ~2–3 weeks out (2026-09-18). This is the product's moat; nothing
else differentiates ALICE from a nicely-voiced UEX client.

**Auspex** is the agent that runs this pipeline — the only genuinely autonomous component
in the system, working its own queue on its own schedule. Role boundaries and why it's an
agent when Uplink isn't: `docs/intent/agent-roles.md`.

## Problem

Trade data ages badly and Star Citizen's economy moves faster than any public source
tracks. One Ironclad or Hull-C emptying a terminal changes supply and demand in seconds —
a route worth flying at the moment of recommendation can be worthless, or unexpectedly
better, by arrival.

UEX and StarHead impose a structural 24–48h trust-gated delay before submitted data
appears. That delay is inherent to how they establish trust, not something they'd remove.
It's the gap ALICE exploits.

## Desired outcome

Pilots run ALICE; ALICE captures commodity kiosk buy/sell prices and stock as they trade;
extraction happens server-side; the pool is served back to every ALICE user immediately,
and pushed upstream to UEX/StarHead where it surfaces on their normal delay. ALICE users
get the freshness edge as a side effect of that delay, not by withholding anything.

## Decided

- **VLM, not classical OCR** — settled by testing, not preference. Classical OCR
  (region-crop + text extraction) topped out around **80% accuracy under optimal
  conditions**. Three causes, all specific to SC's diegetic UI: the panel is semi-opaque
  so the 3D scene behind it bleeds through and blurs glyphs; head-tilt/parallax shifts
  text out of fixed capture regions; scroll position varies. GPT-4/5 vision against a
  generous region crop was significantly more consistent. Don't revisit this without new
  evidence.
- **Dedicated GPU, separate queue from companion inference.** Opposite latency profiles —
  voice is latency-critical, OCR is throughput work that can lag minutes unnoticed.
  Sharing one card means either stalling a pilot's reply or evicting the chat model from
  VRAM and paying a reload.
- **Keep the raw images**, alongside extraction, model version, and confidence. Gives a
  re-processable corpus when extraction improves or the schema changes, an eval set, and
  an audit trail back to the pixels for any disputed value.
- **Images stay local to Auspex.** Only extracted rows travel to the database host.
  Images are the heavy payload and never need to leave the box that processes them —
  cheaper and a cleaner privacy story.
- **v1 capture is a user keybind** ("screenshot the data"). Automatic capture is v2.
  Safest posture under CIG ToS and consistent with `project-information-not-decisions`:
  the pilot acts, ALICE doesn't watch the screen on its own initiative.
- **Auto-capture (v2) does not contradict "automatic arrival detection — out."**
  `docs/presence-layer.md` settled that ALICE never infers a milestone; the pilot reports.
  These look like the same question and aren't: capturing kiosk data **observes**, while
  detecting arrival **advances run state**. Observation changes nothing the pilot is
  accountable for; inference would commit them to a transition they didn't declare. Auto
  capture stays compatible with the principle as long as it never advances a leg on its
  own — recorded here so the distinction is deliberate rather than rediscovered mid-
  argument later.

### Extraction validation (settled 2026-09-18)

Extraction produces a structured record per commodity row —
`Iron; High Inventory; 1,234 per SCU; 12,345 units available` — and validation operates on
those fields, not on the image as a whole.

- **One pass by default; escalate only on a failed smell test.** Most extractions are
  unremarkable and consistent with history. Running N passes on every image triples GPU
  load for no gain on the easy majority.
- **Smell test is volatility-scaled**, using the UEX per-terminal volatility already
  fetched and shown in the workbench. A flat "reject >Nx swing" would fight legitimate
  movement at genuinely volatile terminals; scaling the acceptable band to that terminal's
  own volatility keeps the bound tight where tight is correct.
- **Second pass is contextless.** It re-extracts independently from the same image, with
  no knowledge of the first pass's answer, and the two structured results are compared
  programmatically. This is self-consistency, not self-reflection — a model shown its own
  prior answer is strongly biased toward confirming it, so "do you agree?" measures
  agreeableness rather than digits.
- **Escalate to a manual review queue after 3 passes** without resolution. Never discard:
  store flagged, with every disagreeing extraction and the source image.
- **Accept per row, not per image.** Fields that agree with high confidence are submitted;
  only the specific disagreements are flagged. Iron failing doesn't invalidate Aluminum.
- **Row is the atom — don't go finer.** Partial acceptance *within* a row (taking the
  price while flagging the quantity) is tempting but weaker: self-consistency catches
  stochastic errors, not systematic ones. Two independent passes that both misparse a
  scroll-cut or misaligned row will agree with each other while both being wrong, so a
  disagreement anywhere in a row is evidence the whole row may be structurally suspect.
- **Record the pass count on each accepted value.** Cheap metadata that makes extraction
  quality measurable over time and shows whether a model swap actually helped.

**Manual review runs through a private Discord channel** (Jeff + moderators), chosen
because a review queue only works if it actually gets worked — Discord is already where
he and the community are, already on his phone, already solving notification. A bespoke
review UI would be ergonomically better and would sit untouched.

- **Discord is the view, not the queue.** The authoritative queue is in Postgres; the bot
  posts from it and writes corrections back. Treating Discord as the queue loses items to
  deleted messages, channel history limits, and outages.
- **Post the cropped commodity table, never the full screenshot.** A full capture can
  contain handle, org, location, cargo, and chat. This is the one place the
  "images stay local to Auspex" decision above gets relaxed, so relax it as narrowly
  as possible. Note Discord CDN URLs are unauthenticated — fine for a commodity table,
  not for a full screen.
- **Record who corrected what.** Moderator corrections propagate into every user's route
  recommendations — the only place in the system where one person's input silently changes
  what others see. Attribution is free now and unreconstructable later; it's what makes a
  careless moderator's edits findable and revertible.
- **Queue volume is a health metric, not a staffing problem.** A filling review channel
  means extraction quality is degrading; the fix is upstream, not more moderators.

**Free deterministic checks, before spending a second VLM pass.** Three of the four fields
can be validated without the GPU:

- *Commodity name* — fuzzy-match against `cache.commodities` with the existing
  `resolve_or_hedge`/`match_by_name_or_code` helpers. A name matching nothing known is an
  immediate flag, and the match score is a graded confidence signal for free.
- *Inventory label vs. units* — SC displays both a categorical ("High Inventory") and a
  number. "High Inventory" alongside 12 units is internally inconsistent and catchable
  within a single pass. This one falls out of the UI design at zero cost.
- *Price* — the volatility-bounded check above.

Note *units available* is the weakest field for plausibility checking: stock legitimately
swings to zero or to cap within minutes, so a volatility bound on it is far noisier than
on price. Expect more escalations there and don't over-tune to suppress them.

## Constraints

- **Extraction errors are the real quality risk, not staleness.** A committed route is
  never re-evaluated mid-flight (deliberate — the pilot discovers the market on arrival,
  and a change seconds before touchdown isn't actionable anyway). So aged values are
  acceptable and expected. A *misread* value is different: it was never true, and because
  routes rank by profit/hour, a dropped or added digit is promoted straight to the top
  recommendation. Outliers are actively selected for by the ranking.
- **Stock figures are systematically biased pre-purchase.** Pilots would have to reopen a
  terminal after buying to report what's left, and won't. Known limitation; document it
  rather than pretend the stock number is post-trade truth.
- Screenshots may contain handle, org, or location. Be explicit with users about what
  leaves their machine.
- Current capture approach is **screen polling**, scoped to the window between a pilot
  declaring arrival and completing acquisition/sale on an active run. Still generates many
  useless frames that have to be cheaply discarded from the queue.
- Not all pilots use ALICE, so the pool will always be partial. Weighted station coverage
  (a handful of terminals carry the valuable routes) is what makes partial coverage still
  beat the alternatives — see `project-product-thesis`.

## Open questions

- **A trigger for automatic mode that doesn't flood the queue.** Polling during the
  purchase/sale window is too broad. Needs something cheaper than "VLM decides" to reject
  a frame — the reject path has to cost near-nothing.
- **Validation thresholds.** The shape is settled (see Decided); the numbers aren't. How
  wide is the volatility-scaled band before a value is suspect, and does that band differ
  per field? Tune against real captures rather than guessing up front.
- **Disclosure to users** that flagged captures may be reviewed by moderators. Needed
  before charging money, and it follows from the review-channel decision below rather than
  being optional. Wording is open.
- **StarHead collaboration.** Meeting scheduled. They already run a data-collab with UEX
  and may take on hosting/processing. If they do, the entire downstream half of this
  pipeline changes shape — **don't over-build the upstream push before that resolves.**
- **Upstream push cadence** — batched hourly/daily in chunks vs. streaming. Batched is
  politer to their infrastructure and almost certainly sufficient; at ~10 users volume is
  not a concern either way.
