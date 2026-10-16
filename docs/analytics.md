# Usage analytics (for Grafana)

**Status:** the data is collected (2026-10-12); the dashboard isn't built yet. These are the notes
for building it in Grafana once there are pilots to look at.

**This is the usage and cost slice of [intent/observability.md](intent/observability.md)** (decided
2026-09-18), which sets the overall plan: where Grafana runs, what pages Jeff, and LangSmith vs.
Grafana. Where they overlap, that doc wins. In short:
- **Grafana runs on the FrankenRack Pis at home.**
- **Alerting runs from Hetzner,** with a dead-man's switch to an external service, so a dark house
  still pages.
- **Grafana answers "is something degrading" across all users; LangSmith answers "what happened
  in this one conversation"** (sampled low).

**The questions it answers:**
- How much of ALICE runs on our own hardware vs. paid cloud APIs, and what each costs.
- How close heket runs to its limits.
- When buying hardware beats paying for the cloud.

## Data sources

All in the production Postgres (`graphling` DB on the Hetzner box), one row per call. Usage
metadata only, never text.

| Table | Written by | One row per |
|---|---|---|
| `llm_usage` | `server/llm_gateway_service.py` | model call (classify / respond) |
| `tts_usage` | `server/tts_service.py` | speech request |

**Shared columns:**
- `user_id` (FK to `users`), `created_at`
- `backend`: `heket`, `anthropic` or `openai` for LLM; `heket` or `elevenlabs` for TTS
- `latency_ms`
- `status`: `ok` or `error`
- `fallback_reason`: `down`, `timeout`, `5xx`, `overflow` or `digest_mismatch`; NULL means
  heket answered. `overflow` is *planned peak diversion*; every other value is a
  *failure fallback*. They're tagged separately on purpose: [intent/model-stack.md](intent/model-stack.md)
  says they must never share a counter.
- `cost_usd`

**LLM-only:** `role`, `model`, `prompt_tokens`, `cache_read_tokens`, `completion_tokens`, and
`inflight_at_start` (requests already running on heket when this one arrived).

**TTS-only:** `voice`, `characters`, `audio_seconds`.

**What `cost_usd` means:**
- **Cloud rows:** what the provider bills, from `model_pricing.MODEL_PRICING`. ElevenLabs is
  characters × `ELEVENLABS_USD_PER_1K_CHARS`, which is 0 while on the free tier. Characters are
  stored either way.
- **heket rows:** the electricity used *above idle*, i.e. call duration × 210 W × the
  tariff (€0.34/kWh × `USD_PER_EUR`, see `model_pricing.py`).
- **The box's idle draw is not in any row.** It's ~80 W, €0.66/day, ~€20/month. It's the fixed
  cost of having the box on, so show it as its own constant line, not spread over calls.

The client also writes `turn_metrics.jsonl` per pilot (per-stage timing incl. Whisper, TTS
first byte, and round trip). It's not on the server yet; shipping it there is a later step (see the
`project_turn_timing_analytics_future` memory).

## Panels

Every query uses Grafana's Postgres macros (`$__timeFilter`, `$__timeGroupAlias`).

**Requests and spend by backend**, local vs. cloud over time:
```sql
SELECT $__timeGroupAlias(created_at, '1d'), backend, count(*) AS requests, sum(cost_usd) AS usd
FROM llm_usage WHERE $__timeFilter(created_at) GROUP BY 1, 2 ORDER BY 1;
```
Make the same panel for `tts_usage`. The headline stat is the **local share**:
`count(*) FILTER (WHERE backend = 'heket')::float / count(*)`.

**Why calls left heket:**
```sql
SELECT fallback_reason, count(*), sum(cost_usd) AS usd
FROM llm_usage WHERE $__timeFilter(created_at) AND fallback_reason IS NOT NULL GROUP BY 1;
```
- `overflow` means the box was full, which is a capacity signal. It's capacity management
  working as designed, so it never pages.
- `down`, `timeout`, `5xx` and `digest_mismatch` mean it was off or broken. That's the
  **failure-fallback rate**, a priority alert (below), not just a panel.

**Concurrency**, the number guessed at before launch (about 2.5% busy per active pilot) and now measured:
```sql
SELECT inflight_at_start, count(*) FROM llm_usage
WHERE $__timeFilter(created_at) AND inflight_at_start IS NOT NULL GROUP BY 1 ORDER BY 1;
-- peak per hour:
SELECT $__timeGroupAlias(created_at, '1h'), max(inflight_at_start) + 1 AS peak_concurrent
FROM llm_usage WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1;
```

**Latency by backend and load**, to see whether sharing the box slows turns more than overflowing to Haiku would:
```sql
SELECT backend, least(inflight_at_start, 3) AS load,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
FROM llm_usage WHERE $__timeFilter(created_at) AND status = 'ok' GROUP BY 1, 2 ORDER BY 1, 2;
```

**Cost per active user per day**, the solvency number:
```sql
WITH spend AS (
  SELECT user_id, created_at, cost_usd FROM llm_usage
  UNION ALL
  SELECT user_id, created_at, cost_usd FROM tts_usage
)
SELECT $__timeGroupAlias(created_at, '1d'), sum(cost_usd) / count(DISTINCT user_id) AS usd_per_active_user
FROM spend WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1;
```
Add the idle €0.66/day divided by that day's active users for the all-in figure.

**ElevenLabs characters this month** against the free tier (~10,000/month):
```sql
SELECT sum(characters) FROM tts_usage
WHERE backend = 'elevenlabs' AND created_at >= date_trunc('month', now());
```

## Hardware decision signals

| Signal | Query basis | What it suggests |
|---|---|---|
| Overflow cost per month | `sum(cost_usd)` where `fallback_reason = 'overflow'` | If it nears the monthly cost of more GPU capacity, buy capacity. |
| p95 latency at load 2–3 vs. Haiku's | latency panel | If shared turns are slower than Haiku, lower `LLM_MAX_INFLIGHT` (speed) or add a GPU. |
| Outage fallbacks (`down`/`timeout`/`5xx`) per month | fallback panel | Reliability: UPS, a second box, or a rented Kokoro CPU box for voice continuity. |
| Local share of TTS | TTS requests by backend | Low = ElevenLabs is covering outages. Count the characters against the plan. |
| Turns/month vs. break-even | `count(*)` of respond calls | The box beats Haiku-only at roughly 18k–55k turns/month (docs/heket-bench.md). |

## Alerts (from intent/observability.md)

- **Failure-fallback rate and spend:** fallbacks excluding `overflow`. Every one is a paid call a
  local model was meant to serve, so the threshold is money. Alert when failure-fallback
  `sum(cost_usd)` over a week passes a set figure. Tune it against real usage, don't guess.
- **Hard spend caps come before alerts.** The planned `FALLBACK_DAILY_USD_CAP` in
  [llm-gateway.md](llm-gateway.md) (Limits) bounds a runaway weekend. An alert only tells you
  about it.

## Gaps against intent/observability.md (not collected yet)

- **Time to first token.** The observability doc names *TTFT under concurrency* as the
  hardware-purchase trigger. Today `llm_usage.latency_ms` is the whole call. The gateway already
  streams heket's reply, so adding a `first_token_ms` column is a small change: stamp
  the first chunk. Do it before relying on the latency panel for a purchase decision.
- **Decline rate by classify `reason`** (guardrail health) is client-side, in the graph's
  classify_topic. It never reaches the server. It needs either the turn-metrics upload below,
  or the gateway reading the classify response.
- **Client turn timings** (`turn_metrics.jsonl`: Whisper, TTS first byte, round trip). Uploading
  them was deferred on 2026-10-01 ([voice-stage-timing.md](voice-stage-timing.md)) until there
  are several pilots. The plan is one more table in this Postgres, written best-effort.
- **Product signals** (routes recommended vs. runs started, runs started vs. finalized) and
  **extraction health** (OCR, not built). These come from the ledger tables and the future
  OCR pipeline, not from these usage tables.

## Setting up Grafana (when it's time)

- **Where:** on the FrankenRack Pis, per intent/observability.md.
- **Postgres stays private.** It isn't exposed outside Docker today, on purpose. Reach it from
  the Pis over Tailscale: publish Postgres on the Hetzner host's Tailscale IP only, never on
  0.0.0.0.
- **Datasource:** a read-only Postgres role (`GRANT SELECT ON llm_usage, tts_usage, users`),
  never the app's own user.
- **Alerts stay off the Pis.** Alert delivery runs from Hetzner, because a dashboard at home
  can't page about the home being dark.
- **Keep the dashboard JSON in this repo**, so panels are reviewed like code.
