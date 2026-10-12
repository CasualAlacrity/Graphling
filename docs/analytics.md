# Usage analytics (for Grafana)

**Status:** the data is collected (2026-10-12); the dashboard isn't built yet. These are the notes
for building it in Grafana once there are pilots to look at.

**The questions it answers:**
- How much of ALICE runs on our own hardware vs. paid cloud APIs, and what each costs.
- How close frankencompute runs to its limits.
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
- `backend`: `frankencompute`, `anthropic` or `openai` for LLM; `frankencompute` or `elevenlabs` for TTS
- `latency_ms`
- `status`: `ok` or `error`
- `fallback_reason`: `down`, `timeout`, `5xx`, `overflow` or `digest_mismatch`; NULL means
  frankencompute answered
- `cost_usd`

**LLM-only:** `role`, `model`, `prompt_tokens`, `cache_read_tokens`, `completion_tokens`, and
`inflight_at_start` (requests already running on frankencompute when this one arrived).

**TTS-only:** `voice`, `characters`, `audio_seconds`.

**What `cost_usd` means:**
- **Cloud rows:** what the provider bills, from `model_pricing.MODEL_PRICING`. ElevenLabs is
  characters × `ELEVENLABS_USD_PER_1K_CHARS`, which is 0 while on the free tier. Characters are
  stored either way.
- **frankencompute rows:** the electricity used *above idle*, i.e. call duration × 210 W × the
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
`count(*) FILTER (WHERE backend = 'frankencompute')::float / count(*)`.

**Why calls left frankencompute:**
```sql
SELECT fallback_reason, count(*), sum(cost_usd) AS usd
FROM llm_usage WHERE $__timeFilter(created_at) AND fallback_reason IS NOT NULL GROUP BY 1;
```
- `overflow` means the box was full, which is a capacity signal.
- `down`, `timeout` and `5xx` mean it was off or broken, which is a reliability signal.

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
| Turns/month vs. break-even | `count(*)` of respond calls | The box beats Haiku-only at roughly 18k–55k turns/month (docs/frankencompute-bench.md). |

## Setting up Grafana (when it's time)

- **Datasource:** a read-only Postgres role (`GRANT SELECT ON llm_usage, tts_usage, users`), never
  the app's own user.
- **Postgres isn't exposed outside Docker today, on purpose.** Run Grafana as one more container on
  the same compose network, or give Grafana Cloud access over Tailscale.
- **The Hetzner box has ~2.7 GB of RAM free.** Grafana itself is ~200 MB, which fits.
- **Keep the dashboard JSON in this repo**, so panels are reviewed like code.
