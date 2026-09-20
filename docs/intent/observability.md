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

## Tracing is not monitoring — and it's priced per event

LangSmith bills usage-based at **$5 per 1,000 traces** beyond the 5,000-trace free tier.
A trace is one user utterance: `graph.ainvoke` opens the root and classify / respond /
tool / respond all nest inside it. So at roughly 20 utterances a session and 3 sessions a
week:

| | traces/user/month | cost/user/month |
|---|---|---|
| full tracing | ~240 | **$1.20** |
| 10% sampling | ~24 | $0.12 |
| 5% sampling | ~12 | $0.06 |

**At full tracing that costs more per user than the LLM does.** Chat inference is
electricity once it's on FrankenLab, and the hosted classifier runs about $0.34/user/month
at the same volume — so observability would outweigh the product working. The free tier
tells the same story: 5,000 traces covers ~20 pilots at full rate, or ~200 at 10%.

**Decided: sample low by default, and flip a specific user to full tracing on demand.**

Blanket sampling alone is the wrong answer, because it fails exactly when it matters. At
10%, a pilot reporting "ALICE sent me to the wrong station last night" has a 90% chance
that trace doesn't exist. Sampling is good at aggregate health and useless for the one
conversation you actually need to read.

So: `LANGSMITH_TRACING_SAMPLING_RATE` low for everyone, and per-request tracing control
(`langsmith.tracing_context`, the same mechanism that switches the overlay off) to enable
full traces for one client while a reported problem is being reproduced. Aggregate cost of
a 5% sample, complete data for the case under investigation.

**The division of labour this implies:**

- **Grafana answers "is something degrading"** — continuously, across all users, at no
  per-event cost. Extraction failure rates, fallback rate, queue depth, latency.
- **LangSmith answers "what happened in this one conversation"** — selectively, when
  something specific needs explaining. Which tool was chosen and why, what the model saw.

Neither should be paying to watch the other's problem. A trace is a debugging artefact
about a single turn; a metric is a health signal about the system. Using traces as
monitoring is what makes the bill scale with users instead of with incidents.

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
- How a client gets flipped into full-trace mode — an env var and a restart is the cheap
  version; doing it without interrupting a pilot mid-run needs the API tier, since that's
  where a per-user setting would live.
- Whether extraction metrics live in the same Grafana instance as infrastructure metrics,
  given the DB is on Hetzner and Auspex runs at home.
