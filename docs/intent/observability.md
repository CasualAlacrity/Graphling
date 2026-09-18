# Intent — observability and alerting

**Status:** not started (2026-09-18). Hardware is on hand (Pis, 2.5" SSDs for FrankenRack);
nothing is instrumented yet.

## Problem

There is no monitoring of any kind, and the moment user #2 isn't Jeff, the first
notification of an outage is a user complaining. He is also the only on-call.

Worse for the product specifically: **extraction quality can degrade silently.** The moat
is fresh *accurate* data — if the OCR pipeline starts misreading after a Star Citizen
patch, nothing surfaces it, and the core product claim quietly becomes false while
everything still looks like it's working.

## Desired outcome

Dashboards for diagnosis, and an alerting path that survives the failures it reports on.

## Decided

- **Grafana (or similar) on the FrankenRack Pis** for dashboards. The Pis are useless for
  inference — no CUDA, nowhere near the memory bandwidth — but monitoring is real work
  they're suited to, using hardware already in the house.
- **The alerting path cannot live at home.** Pis sharing power and network with FrankenLab
  means one outage takes out both the GPU box and the thing that would report it. Shape:
  Hetzner monitors FrankenLab, and a dead-man's switch runs from Hetzner out to an
  external service — so a completely dark house still pages. Dashboards at home, alert
  delivery elsewhere.
- **Provider fallback rate is a priority alert, not a dashboard curiosity.** It's
  simultaneously a reliability signal and a cost signal: every fallback is a paid API call
  that a local model was supposed to serve. A trending rate is money leaking, so the
  threshold isn't arbitrary — if fallbacks cost more than some weekly figure, the local
  setup has stopped earning its keep, which is the same alert reliability would want.
- **Decompose fallback by trigger**, tagged where it fires: unreachable, model cold/not
  loaded, timeout under load, inference error/OOM. Four different problems with four
  different responses — a trending composite says something is wrong, a tagged one says
  what. Also kept distinct from **planned peak diversion**, which is capacity management
  working as designed and must not page anyone (see `docs/intent/model-stack.md`).
- **Pair it with a latency distribution.** `with_fallbacks()` fires on exception, so a
  model that is degraded but alive — time-to-first-token drifting from 1 s to 8 s — never
  appears in fallback rate at all. Users feel it; the dashboard stays green.
- **Annotate Star Citizen patch days on the extraction dashboards.** A patch shifting the
  UI is the single most likely cause of a step change in extraction failures, and it's a
  discrete predictable event. Correlating the two turns "why did escalations triple on
  Tuesday" into a five-second answer.
- **Review-queue volume is a health metric, not a staffing problem.** A filling Discord
  review channel means extraction quality is degrading; the fix is upstream, never more
  moderators.
- **User-facing status goes to Discord, never through ALICE.** If Auspex is down or
  backed up and pilots need telling, a status bot — *Chris Roberto*, no relation to Chris
  Roberts — posts it to a public Discord channel. ALICE never mentions it, which keeps the
  decoupling intact (`docs/intent/agent-roles.md`): she'd otherwise need awareness of
  infrastructure she has no business having, and an assistant apologising for backend
  problems breaks the persona. Ops comms and product voice stay separate.
  - Same alert, two audiences: the signal that pages Jeff can post a plainer version
    publicly. **Separate channels though** — the private review channel carries flagged
    extractions and moderator corrections; a public status channel carries "the pool is
    behind right now." Different readers, different content.

## Signals worth carrying (system-specific)

General monitoring practice isn't the gap here — Jeff's LiveOps background covers it.
These are the ones particular to where *this* system breaks:

- **Extraction:** first-pass smell-test failure rate, second-pass disagreement rate, review
  queue depth *and age of oldest item*, pass-count distribution on accepted values. Trend
  matters far more than absolute.
- **Inference:** time-to-first-token and tokens/sec on FrankenLab, fallback rate by tag,
  concurrent request count. **TTFT under concurrency is the hardware-purchase trigger** —
  it's the number that answers "buy more, or divert at peak," which is why capacity was
  deliberately left to measurement rather than specified up front.
- **Guardrail:** decline rate, dimensioned by the classifier's `reason` field. If declines
  spike, `classify_topic` is misfiring and today there'd be no way to see it short of a
  user reporting that ALICE got weird. That `reason` field exists for tracing and is what
  makes this queryable — see `docs/intent/classify-topic-rework.md`.
- **Product:** routes recommended vs. runs actually started (is the advice taken?), runs
  started vs. finalized (abandonment).

## Open questions

- Alert thresholds for everything above — tune against real usage, don't guess.
- Which external service backs the dead-man's switch.
- Whether extraction metrics live in the same Grafana instance as infrastructure metrics,
  given the DB is on Hetzner and Auspex runs at home.
