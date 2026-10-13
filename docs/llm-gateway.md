# LLM gateway — self-hosted models behind the ledger server

**Status:** LLM gateway built 2026-10-09 and deployed. TTS gateway (Kokoro on frankencompute) built and deployed 2026-10-12. UEX calls moved server-side, runaway limits and client re-login built 2026-10-12 (not yet deployed). Usage analytics: docs/analytics.md.
**Measured costs and choices** (power, local model shoot-out, local TTS = Kokoro `bf_isabella`,
3 parallel slots): [frankencompute-bench.md](frankencompute-bench.md).
**Goal:** the client ships as a whole product. 10 new pilots using ALICE within 2 weeks (by
~2026-10-23), with nobody added by hand. Discord login is the only onboarding step.

**Beta access gating (built 2026-10-12).** Only Discord accounts that are in the ALICE Discord
server *and* hold the beta role get a JWT. It's two server settings, `BETA_GUILD_ID` and
`BETA_ROLE_ID` (`server/config.py`); an empty `BETA_GUILD_ID` turns gating off. **At release: clear
`BETA_GUILD_ID` and redeploy.** No code change, no migration, and existing accounts are untouched,
because beta status is never stored.
- **Where:** only the Discord callback (`server/routes/auth.py`). While gating is on, login asks for
  `identify guilds.members.read` (just `identify` when off). After the code exchange, the callback
  calls `GET /users/@me/guilds/{guild}/member` with the pilot's own access token, so no bot has
  to be in the server. The access token is then discarded, never stored.
- **Refused:** no token and no `users` row. The browser goes back to the client's listener with
  `?error=not_in_beta&missing=server|role` (not in the Discord server, or in it without the
  role) or `?error=beta_check_failed` (any other Discord answer, including errors and rate
  limits, so it **fails closed**). `auth/discord_identity.py` raises `LoginRefused`, and the
  client prints the reason and exits. The browser tab shows a branded page with the same words
  (`auth/login_pages.py`, one page per outcome: signed in, missing role, not in server, check
  failed, cancelled, failed). A Cancel on Discord's consent screen is forwarded the same way
  (`?error=access_denied`).
- **Waitlist:** every refusal logs `beta sign-in refused: discord_id=… reason=… at=…` (a
  warning on the server's stderr): `docker compose logs server | grep "beta sign-in refused"`.
  Container logs don't survive the container being recreated (every deploy), so copy them out
  before deploying if they matter.
- **Renewal re-checks for free:** the callback is the only place a JWT is minted, and both
  client renewal paths (pre-emptive at startup, re-login after a 401) go through the browser
  login. **Not revocation:** removing the role takes effect at the pilot's next login, so an
  issued token keeps working for up to `JWT_EXPIRE_MINUTES` (7 days).
- Discord is the identity provider only. Nothing else (tiers, features, names) reads the role.

**Growth model:** grow users slowly and watch cost per active user across every paid or
self-hosted service: frankencompute (electricity, already estimated in `turn_metrics`), hosted LLM
fallback, ElevenLabs, and Jev if `classify_topic` moves to it. **ElevenLabs is the expected
bottleneck.** The point of metering is to find *how many active users* it takes before it hurts,
not to cap it early. So every server-side metered row records `user_id` + units + cost, and a
single query answers "cost per active user per day, by service".

## Why

Today every pilot's ALICE calls an LLM directly from their own machine (`get_chat_llm()` in
`app/llm.py`). That means each pilot either runs Ollama locally or holds a hosted API key. Neither
works for paying users. **frankencompute** (Threadripper 1900X, 64 GB, RTX 4080 16 GB, Ubuntu
Server, built 2026-10-08) can serve gemma4 at about **2.7–3.4 s median per turn**, versus 15.3 s on the
Mac (eval runs `evals/agent_eval/results/ollama-gemma4__20261009-09*.json` vs `…20261006-092416.json`).
Caveat: those aren't the same weights. The box pulled a fresh `gemma4:latest` (dc35e8d9c606,
6.6 GB), while the Mac's is a 3-month-old build (c6eb396dbd59, 9.6 GB). So part of the speedup may
be the model, not the hardware. Either way it must not be reachable by users directly.

Same move as Phase 2 made for Postgres: **users talk only to the server, and the server talks to
everything else.** The ledger server already authenticates every request with Discord OAuth + JWT
(`server/dependencies.get_current_user`). The gateway is one more router behind that same
dependency.

```
ALICE client ── HTTPS + JWT ──▶ api.heyalice.help (Hetzner, nginx → server container)
                                   │  /llm/api/chat (Ollama native format)
                                   │  auth · rate limit · usage log · routing
                    ┌──────────────┴───────────────┐
                    ▼ primary                       ▼ fallback
          frankencompute Ollama               OpenAI (gpt-4o-mini)
          (private link, never public)        (key lives only on the server)
```

The agent itself does not move. LangGraph, tools, voice and overlay all stay client-side, as
CLAUDE.md says. The server only relays model calls; it doesn't run the graph.

## Decisions

| # | Decision | Recommendation | Why |
|---|---|---|---|
| 1 | Wire format | **Ollama-native** `/llm/api/chat` (changed 2026-10-09, was OpenAI-compatible) | Ollama's OpenAI-compatible endpoint silently drops gemma4's thinking: `reasoning_effort: high` produced 0 reasoning tokens in 0.08 s, while native `think: true` produced ~570 characters. The evals showed reasoning helps the default model. So the client keeps `ChatOllama` exactly as evaluated, pointed at `<ALICE_API_URL>/llm`, and the frankencompute path is a pure pass-through. Only the OpenAI fallback translates (Ollama ⇄ OpenAI chat-completions). |
| 2 | Who picks the model | **The server.** The client sends a *role* (`alice-chat`, `alice-classify`), never a real model name | Lets you swap models, flip reasoning, or change fallback without shipping a client update to 10 people. Also stops a client from asking for an expensive model. |
| 3 | Fallback provider for v1 | **OpenAI only.** Anthropic is v2 | OpenAI is the same format, so fallback is about 10 lines. Anthropic needs request/response translation, and the existing `cache_control` breakpoints (`anthropic_cache_mode`) wouldn't survive an OpenAI-format hop. |
| 4 | Box ↔ Hetzner link | **Tailscale on exactly two machines** (Hetzner + frankencompute) | See below. Users never touch it; it's machine-to-machine infra you set up once. |
| 5 | Streaming | **Pass-through stream from frankencompute; fallback replies in one chunk** | `ChatOllama` always streams internally, so the gateway relays the box's NDJSON. Fallback decisions happen before any byte is sent (connect error / timeout / 5xx on the request), so retrying elsewhere stays clean. A mid-stream failure is recorded as an error, not retried. |
| 6 | Reasoning flag | **Server-side, per role** (chat: on, classify: off) | Matches today's `OLLAMA_REASONING` / `OLLAMA_CLASSIFICATION_REASONING` defaults, moved to where the model choice now lives. |

### Why Tailscale here after all

The earlier idea was a reverse SSH tunnel from frankencompute to Hetzner. The catch is that the
server runs in Docker. A reverse tunnel binds to the Hetzner host's loopback, which the container
can't reach without `GatewayPorts` plus `host-gateway` plumbing. With Tailscale on both hosts, the
container reaches frankencompute's Tailscale IP through the host's normal routing. The objection to
Tailscale was having to add each *user* to it, and that doesn't apply: it's two machines you own,
added once. Tailscale ACLs can limit it further, to Hetzner → frankencompute:11434 only.

## Server

### Endpoint

`POST /llm/api/chat`, behind `Depends(get_current_user)`: `server/routes/llm.py` (HTTP) +
`server/llm_gateway_service.py` (routing, translation, usage). Body cap 512 KB.

Request: what `ChatOllama` sends. `model` must be a role name (400 otherwise). The server
replaces `model` and `think` per role, drops `keep_alive`, and keeps only harmless `options`
(temperature, top_p, top_k, seed, stop, num_predict, repeat_penalty), so one pilot can't send
`num_ctx=131072` or `keep_alive=0` and evict the model for everyone.

Response: Ollama NDJSON. The final (`done: true`) chunk gets **`alice_backend:
frankencompute|openai`** and the real `model`. `ChatOllama` copies that chunk into
`generation_info`, and `turn_metrics._answered_by` uses it to price each call by what
actually answered.

### Role table (server config, not client)

```python
ROLES = {
    "alice-chat":     [Backend("frankencompute", "gemma4", reasoning=True),
                       Backend("openai", settings.fallback_chat_model)],
    "alice-classify": [Backend("frankencompute", "gemma4", reasoning=False),
                       Backend("openai", settings.fallback_classify_model)],
}
```

**Pin the exact model build, not just the tag.** On 2026-10-09 `gemma4:latest` meant three
different builds across three eval runs (Mac old 9.6 GB, Mac new nvfp4 safetensors, box GGUF
Q4_K_M), and they scored 39/45, 29/45 and 36/45 on the same cases. The new Mac build printed raw
tool-call text instead of making calls. So the role table names a model *and* the digest it was
evaluated at (`gemma4@dc35e8d9c606` on frankencompute today), and the gateway's startup health
check refuses to route to frankencompute if `/api/tags` reports a different digest. Never
`ollama pull` on the box without re-running the eval first.

Tried in order. Note: gemma4 decides per call whether to think even with `think: true`
(the same request directly to the box produced no thinking 2 times out of 6), so "no
thinking in this response" isn't a gateway bug.

### Fallback rules

Fall back to the next backend **only when frankencompute can't answer**, never because the answer
looks bad:

- connect error / connection refused / DNS failure;
- connect timeout (**2 s**) or total timeout (**30 s**, since eval p90 is 5.1 s and max 15.4 s);
- HTTP 5xx from Ollama;
- **overflow**: frankencompute already has `MAX_INFLIGHT` requests running (see Capacity).

A 4xx from a backend (bad request) is returned to the client as-is, not retried. Retrying would
just double the cost of a bug.

**Circuit breaker:** after a connect failure, mark frankencompute *down* for 30 s and send
everything straight to fallback. That way every turn doesn't pay the 2 s timeout while the box is
off. A background task probes `GET /api/version` every 15 s and marks it *up* again. In-memory
state is fine: one server process, the same assumption `_PENDING_LOGINS` already makes.

### Usage log

New table `llm_usage` (Alembic migration):

| column | notes |
|---|---|
| `id`, `created_at` | |
| `user_id` | FK users |
| `role`, `backend`, `model` | what was asked for vs. what answered |
| `prompt_tokens`, `completion_tokens` | from the backend's `usage` |
| `latency_ms` | gateway-measured |
| `status` | `ok` / `error` |
| `fallback_reason` | null, or `down` / `timeout` / `5xx` / `overflow` |

This table is the seed of the subscription story: cost per user, who's active, and how often
fallback (real money) fires. It's the server-side counterpart to the turn-metrics → Grafana idea.

### Limits (cheap insurance for 10 users) — built 2026-10-12

These are runaway guards, not rationing. Every threshold sits well above a real session
(`server/limits.py`, settings in `server/config.py`).

- **Per-user rate limits** (in memory, sliding minute):
  - `LLM_REQUESTS_PER_MINUTE=30`; a normal turn is 2–3 calls.
  - `TTS_REQUESTS_PER_MINUTE=20`.
  - Over the limit: `429` with `Retry-After`.
- **Speech runaway guard:** `TTS_DAILY_CHARS_PER_USER=50000` per pilot per UTC day, in memory.
  A 3-hour session is about 20k characters.
- **Global daily fallback budget:** `FALLBACK_DAILY_USD_CAP`, default $5, summed from today's
  hosted `llm_usage.cost_usd`. Read from the table, so a restart can't reset it; fails open if
  the database can't answer.
  - **Past the cap, an *overflow* queues on frankencompute instead of paying.** The box is busy,
    not broken.
  - **Past the cap, an *outage* gets `503`** ("try again shortly") instead of open-ended billing.
  - **What $5 buys:** about 10–25k Haiku 5.5 fallback calls, roughly $0.0002–0.0005 each.
- **Body size cap:** 512 KB on `/llm/api/chat`, and 2,000 characters on `/tts`.

**Staying logged in (client, also 2026-10-12).** Every call to the server uses
`auth/session_auth.PilotSessionAuth`.
- **On `401`** it opens the Discord login once, even if several requests failed together, then
  retries the request (`relogin_after_rejection`).
- **At startup,** a token with less than 24 h left is renewed straight away. The browser opens
  while the pilot launches ALICE, not mid-run.
- **No per-request check:** the old `/auth/users/me` call before every ledger and UEX request is
  gone.

### Config (new `Settings` fields + `docker-compose.prod.yml` env)

`frankencompute_url` (e.g. `http://100.x.y.z:11434`), `openai_api_key`,
`fallback_chat_model` (`gpt-4o-mini`), `fallback_classify_model` (`gpt-4o-mini`),
`llm_max_inflight` (2), `fallback_daily_usd_cap` (5.0).

## Prompts (added 2026-10-13)

The gateway owns ALICE's prompts; pilots never receive them. Through the gateway
(`LLM_PROVIDER=alice`) the client sends a placeholder system message,
`{{alice-prompt:<name>}}` (`prompt_loader.prompt_messages`), and `routes/llm.py` expands it
(`server/prompt_service.py`) before routing, so frankencompute and both fallbacks see the
real text. The installer no longer bundles `prompts/.prompt_cache.json`. Development
against Ollama/OpenAI/Anthropic directly still loads the text client-side, as before.

Sources: the LangSmith Hub when the server has `LANGSMITH_API_KEY` (+ the EU
`LANGSMITH_ENDPOINT`), else the committed snapshot copied into the image. Pulled at
startup and every `PROMPT_REFRESH_S` (300); a failed pull keeps what's loaded.

**Releasing a prompt:** with `PROMPT_HUB_TAG=prod` set on the server, edit the prompt in
the Hub (pilots unaffected), test it from a dev client, then move the `prod` tag to that
commit — every pilot gets it within five minutes, no redeploy, no new installer. Without a
key on the server, a release is: pull the new prompt on a dev machine (which refreshes the
committed snapshot), commit, redeploy.

**Order matters once:** the server must have this before any client that sends
placeholders — an older server would hand the model the literal placeholder text.
Clients from before the change still send full prompts and keep working.

## frankencompute

Already done 2026-10-09: `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_CONTEXT_LENGTH=32768` (systemd override),
GPU moved to the x16 slot (PCI_E1).

Still to do:

1. Install Tailscale on frankencompute and the Hetzner host. Add an ACL so only Hetzner can reach
   frankencompute:11434.
2. Make Ollama listen beyond loopback: `OLLAMA_HOST=0.0.0.0:11434` in the override. Firewall it
   to the tailnet only: `ufw allow in on tailscale0 to any port 11434`, with the default deny
   for everything else. (Binding straight to the Tailscale IP breaks if Ollama starts before
   tailscaled does.)
3. From **inside the server container** on Hetzner, `curl http://<franken-tailscale-ip>:11434/api/version`.
   This proves the Docker → host → tailnet path before any gateway code depends on it.
4. Capacity: set `OLLAMA_NUM_PARALLEL=2`. Each parallel slot gets its own 32k KV cache, so confirm
   `ollama ps` still shows 100% GPU. If it doesn't, drop the context length before dropping
   parallelism.
5. Power settings: the box must not suspend. Enable BIOS "restore on AC power loss" so a power
   blip brings it back without you.

### Capacity sanity check

10 pilots won't all be online together. Say 5 at peak, each taking a turn every ~1 min, at
~3.5 s of GPU time per turn: the GPU is busy about 30% of the time at peak. Two parallel slots
absorb most collisions. `MAX_INFLIGHT=2` sends the rare third concurrent request to fallback
instead of making a pilot wait in a queue. Revisit when `llm_usage` shows `overflow` regularly.

## Client

1. **`app/llm.py`:** `LLM_PROVIDER=alice` returns `ChatOllama(model="alice-chat" | "alice-classify",
   base_url=f"{ALICE_API_URL}/llm", reasoning=<mirrors the role>)`. `reasoning` only makes
   ChatOllama keep the thinking in history, the same message shape as the evals; the server
   decides whether the model actually thinks.
2. **Token timing:** `graph.py` builds its LLMs at import, and on a first install that happens
   while the pilot is still mid-login. So the JWT isn't baked in: an `httpx.Auth`
   (`_PilotSessionAuth`) reads it from the login cache on every request
   (`auth.discord_identity.cached_session_token`).
3. **Expired token mid-session:** still to do. A 401 from the gateway should go to the same
   re-login path the ledger client uses, not a raw `ResponseError`.
4. `PREWARM_OLLAMA` is already a no-op for any provider but `ollama`, and so is
   `anthropic_cache_mode()`.
5. **`turn_metrics`:** done. `_answered_by` reads `alice_backend` and the model name, so
   fallback calls are priced at OpenAI rates and frankencompute calls on electricity.

## Verification results (2026-10-09)

Same 3 cases × 5 samples throughout, gemma4 `dc35e8d9c606` on frankencompute:

| Path | Passed | Tool / args / location / price | Claims supported | Median turn |
|---|---|---|---|---|
| Direct to box (`ollama-gemma4__20261009-094148`) | 36/45 | 45/45 each | 36/45 | 2.73 s |
| Through local gateway (`alice-gateway__20261009-121904`) | 39/45 | 45/45 each | 39/45 | 3.05 s |

The gateway adds ~0.3 s and changes no behavior (the 36 vs 39 is noise). 137 `llm_usage` rows,
all frankencompute, 0 errors.

**Fallback (box pointed at a dead port):** routing works. Calls answer via gpt-4o-mini tagged
`openai`, with `fallback_reason=down`. But the eval **died on call 4: OpenAI 429, "Limit 5
RPM"** on this project. **The fallback is unusable until the OpenAI account leaves the
free/lowest tier**: 10 pilots × 2–3 calls per turn blows through 5 requests/minute instantly.
Fix before launch: add credit to reach Tier 1 (much higher RPM), or make Anthropic the fallback.
Then re-run this check to get the fallback model's actual scores.

### Fallback model choice (2026-10-09)

The OpenAI fallback is unusable on this account (429 at 5 requests/minute), so the default
fallback is now **Anthropic** (`server/anthropic_fallback.py`, official SDK, prompt caching on
the tools+persona prefix). Same 3 cases × 5 samples, frankencompute forced down:

| Fallback | Tool / args | Claims supported | Median turn | Cost per call (`llm_usage`) |
|---|---|---|---|---|
| **`claude-haiku-5-5` (default)** | 45/45 | 19/45 | 4.9 s | $0.00019 |
| `claude-haiku-4-5` | 33/45 | 16/33 | 4.2 s | $0.00161 |

Haiku 4.5 asked a clarifying question instead of calling the tool in 12/45 turns. Haiku 5.5's
low claims score is mostly the gpt-4o-mini judge flagging accurate comparisons ("Port
Tressler is the alternative at 1,225,000") -- the judge is the weakest part of the eval now.

**Bug found and fixed on the way:** the ollama client strips tool-call ids, so the gateway
receives none. Both fallback translations now pair tool results to calls by position
(`server/tool_call_ids.py`), the same pairing Ollama itself uses.

## Other blockers for 10 users (not the gateway, but they gate the same goal)

The gateway removes the LLM key, but a fresh pilot's `.env` still needs these. Each one is
something a new user can't supply:

**Decided 2026-10-09: every third-party key moves to Hetzner.** After this, a shipped client
holds only `ALICE_API_URL` and the pilot's JWT.

### TTS proxy (Kokoro on frankencompute, ElevenLabs optional) — built 2026-10-12

Changed from the ElevenLabs-only plan after the 2026-10-12 bench (docs/frankencompute-bench.md):
Kokoro `bf_isabella` on frankencompute won by ear, especially on numbers, and costs only electricity.

- **frankencompute:** `frankencompute/kokoro_server.py`, run by the `kokoro-tts` systemd service
  (`frankencompute/kokoro-tts.service`).
  - `POST /tts {"text", "voice"}` returns a whole 24 kHz WAV, with `X-Audio-Seconds`.
  - A typical reply takes ~0.1 s, so nothing streams yet.
  - It listens on :8880; ufw allows that port on `tailscale0` only, the same as Ollama.
- **Server:** `POST /tts {"text"}` behind `get_current_user` (`server/routes/tts.py` →
  `server/tts_service.py`).
  - Tries Kokoro first. On a connect error, timeout or 5xx it benches the box for 30 s and falls
    back to ElevenLabs, but **only if `ELEVENLABS_API_KEY` is set**, because that's a different voice.
  - With no key: 503, ALICE stays quiet for that turn and still answers in the overlay, and every
    request retries the box.
  - A 4xx (empty or too long) is never retried on ElevenLabs.
  - Text is capped at 2,000 characters.
  - The response says which backend answered in `X-Alice-TTS-Backend`.
- **Metering:** one `tts_usage` row per attempt: backend, voice, characters, audio seconds,
  latency, status, fallback reason, cost.
  - Kokoro is priced as electricity, like llm_usage.
  - ElevenLabs is priced as characters × `ELEVENLABS_USD_PER_1K_CHARS` (0 on the free tier).
    Characters are always stored, so a paid plan's cost can be recomputed later.
  - Planned, not built: a **runaway guard** (per-user daily cap, 429), not a usage limit.
- **Client:** `TTS_PROVIDER=alice` sends `voice/tts.synthesize()` to `<ALICE_API_URL>/tts` with
  the pilot's JWT (`auth/session_auth.py`, shared with the LLM gateway).
  - `play_audio` decodes WAV and MP3 alike.
  - Any server failure means empty audio and a printed reason, never a crashed turn.
  - `tts_first_chunk_ms` still measures time to first byte, now including the Hetzner hop.
- **Not needed yet:** `proxy_buffering off` in nginx. Responses are whole files, so there's no
  stream to protect. Add it if Kokoro ever streams sentence by sentence.
- **A Kokoro fallback off frankencompute** (same voice when the box is down) was considered.
  - The Hetzner web host is 2 shared vCPUs that also serve the other apps, too small to do it well.
  - frankencompute's own CPU manages only ~3× real time and dies with the box anyway.
  - If outages make the voice switch annoying, rent a small dedicated CPU box for it.
- STT stays local (Whisper `base` on the pilot's machine, `voice/voice_input.py`). It needs no
  key, adds no server load, and keeps raw audio off the server.

### UEX: all calls server-side — built 2026-10-12

**Done:**
- The four missing routes exist: `/uex-cache/item-prices`, `/vehicle-purchase-prices`,
  `/vehicle-rental-prices` and `/orbit-distances`.
- The client's `UEXCorpClient(via_server=True)` sends every lookup to the server, so the tools
  are unchanged. That covers commodity prices too, which were a live client-to-UEX call before.
- The cache lifetimes for those rows are in place (`price_cache.CACHE_TTL`).
- `UEXCORP_*` is gone from the client `.env-template`.

**Still open:**
- The reference bundle's 7 d + game-version refresh, and the client's bundle version check
  (below).


Reference data and the overlay's price lookups already go through the server
(`uex_cache_client`). But the agent's own tool calls still hit UEX **directly from the client**
through `UEXCorpClient` (`tools/uexcorp/client.py`), with the client's own token:

| Client method | Server route today? |
|---|---|
| `get_commodity_routes` | yes, overlay uses `uex_cache_client.get_commodity_route_rows` |
| `get_commodity_prices` / `get_terminal_prices` | yes, `/uex-cache/commodity-prices`, `/terminal-prices` |
| `get_item_prices` | **no, new route** |
| `get_vehicle_purchase_prices` / `get_vehicle_rental_prices` | **no, new routes** |
| `get_orbit_distances` | **no, new route** (static data, long TTL like reference) |

Work: add the four missing routes to `server/routes/uex_cache.py` + `uex_cache_service.py`, then
point every `UEXCorpClient` method at the server, so the methods keep their signatures and the
tools don't change. Remove `UEXCORP_*` from the client `.env-template`.

**Shared UEX rate limit: accepted (2026-10-09).** Caching keeps volume low, and Jeff has a
direct line to UEX if limits become a problem.

### Cache lifetimes by how often the data actually changes

UEX's own cache times come from each endpoint's `Cache-Control: max-age` header, read live on
2026-10-09. The docs site doesn't list them.

| Data | Cached where today | Our TTL today | UEX max-age | Changes when | **Target** |
|---|---|---|---|---|---|
| Reference bundle: star systems, orbits, moons, POI, item categories, items, commodity statuses | server Postgres + client memory | 24 h | 24 h | patches | **7 d + version refresh** |
| Refinery yields (bonus %) | (in the bundle today) | 24 h | 24 h | patches, then community re-reports for ~2 weeks after | **split out: 24 h (= UEX)** |
| ↳ terminals, vehicles | (same bundle) | 24 h | 12 h | patches; occasional UEX community fixes | (same bundle) |
| ↳ commodities (ids, flags) | (same bundle) | 24 h | 1 h | patches (our cached fields are definitions only, no prices) | (same bundle) |
| Wiki: ship speeds, locations | server Postgres | 24 h | — | patches, wiki edits | **7 d + version refresh** |
| Orbit distances | **client memory, no TTL** | until restart | 24 h | never within a patch | **server, patch lifetime** |
| Item prices | none (live per call) | — | 24 h | game versions only (Jeff, 2026-10-09) | **7 d + version refresh** |
| Vehicle purchase / rental prices | none (live per call) | — | 12 h | game versions only (Jeff, 2026-10-09) | **7 d + version refresh** |
| Commodity prices (by commodity / by terminal) | server Postgres | 30 min | 30 min | player reports, any second | **30 min (= UEX)** |
| Commodity routes (by commodity / by origin) | server Postgres | 30 min | 30 min | player reports | **30 min (= UEX)** |

**Rule of thumb (2026-10-09):** only commodity prices and routes shift regularly (player-driven
economy). Everything else changes with game versions, so it gets 7 days plus the version refresh.
The one exception is refinery yields at 24 h, because of the post-patch reporting wave (below).

Why the targets differ from "same as UEX":

- **Static data: longer than UEX, plus a version trigger.** UEX's 12–24 h are generic hints,
  not how often terminals actually change. Going longer saves the shared rate limit (the
  reference bundle alone is ~10 calls plus one per item category). 7 days, not months, because
  UEX's data is community-corrected: a wrongly flagged terminal (`is_nqa`, `is_auto_load`) fixed
  upstream should reach pilots within a week. `refresh_static_caches.py` stays for patch day.
- **Prices: UEX's 30 min is effectively a floor.** UEX itself asks clients to cache 30 min, so
  polling faster mostly gets the same answer back while spending the shared rate limit. Fresher
  than that can only come from the crowd-sourced OCR pool, where local reports override cached
  UEX rows by recency.
- **Commodity price lookups move from live to the 30-min cache.** Today `commodity_price_lookup`
  calls UEX live from the client. Behind the server it reads the shared cache, which is in
  practice as fresh as UEX's own 30-min caching allows.

**Refinery yields are split out of the bundle.** UEX's 215 yield rows (checked 2026-10-09) were
all last modified between 2026-08-29 and 2026-09-12, in a ~2-week wave after the patch, with
nothing since. So they don't change continuously, but after a patch they keep trickling in for
weeks as pilots report them, and a 7-day TTL would lag that wave. It's one endpoint, so a 24 h
TTL costs one UEX call a day. If yields turn out to change mid-patch in game (Jeff's read), 24 h
still catches it within a day.

**Game-version refresh (new).** `GET /2.0/game_versions` returns `{"live": "4.10.1", "ptu": "4.10.2"}`.
A server background job polls it hourly. When `live` changes, it runs the same refresh as
`refresh_static_caches.py` and clears the item and vehicle purchase/rental price rows. Caveat: that endpoint also sends
`max-age=86400`. If UEX caches it server-side for that long, detection can lag a patch by up to a
day, so keep the manual script for patch day.

**Client copy of the reference bundle.** The client holds the bundle in memory with its own
24 h check (`UEXCorpClient._is_fresh`). Once the server's TTL is a week, the client's copy should
follow the server's version, not the clock: the server returns the bundle's `fetched_at` with a
cheap `/uex-cache/reference/version` call, and the client re-downloads only when it changed.
Otherwise a patch refresh on the server takes up to 24 h to reach a running client.

**Stale comment to fix:** `tools/uexcorp/price_cache.py` says the price cache "only ever answers
'does this terminal/commodity pairing exist at all'". That's no longer true: `best_route` ranks
on cached route rows and the overlay's volatility icon reads cached prices. The 30-min TTL is
still right; the comment isn't.

### Remaining client keys

| Key | Plan |
|---|---|
| `LANGSMITH_*` | Off for users (`LANGSMITH_TRACING=false`). `llm_usage` covers what you need. |

Packaging the client so a non-developer can install it is out of scope here, but it's the other
big gate on "10 users" and needs its own plan.

Privacy: pilots' conversations will now pass through your server and frankencompute. Say so in
plain words at onboarding. Log usage metadata, never message content, unless a pilot opts in to
debug logging.

## Verification (definition of done)

1. **Same behavior through the gateway:** `evals/agent_eval/run.py` with `LLM_PROVIDER=alice`
   against a local server pointed at frankencompute. Pass rate within noise of the direct-Ollama
   run, and median latency ≤ direct + ~300 ms.
2. **Fallback model is acceptable:** the same eval with frankencompute forced down
   (`frankencompute_url` pointed at a dead port). This is the first eval of gpt-4o-mini on the
   current case set. If it's clearly worse, choose a better fallback model before launch, not
   after.
3. **Pull the plug live:** stop Ollama mid-session, take a turn (should succeed via fallback
   within ~2 s of extra latency), restart it, and see traffic return within ~15 s.
4. **Auth:** no token → 401, expired token → 401, unknown role → 400.
5. **Limits:** rate-limit and spend-cap paths return their 429/503 with readable messages.
6. Unit tests for the routing and fallback decision (pure function: backend state + error → next
   action) and for the usage-row writer. No live models in pytest, same rule as the evals.

## Two-week plan

| Days | Work |
|---|---|
| 1–2 | Tailscale + Ollama binding + container curl (frankencompute steps 1–3). Gateway endpoint with frankencompute only, auth, usage table. |
| 3–4 | Fallback + circuit breaker + limits. Client `alice` provider + token timing + metrics. |
| 5 | Verification 1–5. Deploy to Hetzner. |
| 6–7 | TTS streaming proxy + nginx `proxy_buffering off`. Four missing UEX routes, `UEXCorpClient` repointed, `UEXCORP_*` gone from the client. |
| 8–10 | First 3 pilots from the org. Watch `llm_usage`, fix what breaks. |
| 11–14 | Remaining 7 pilots. |

## Open questions

1. Fallback model: gpt-4o-mini (cheap, unevaluated on this case set) or something stronger?
   Decide after verification step 2.
2. Should a pilot ever see that they're on fallback? (Information-not-decisions suggests a small
   overlay indicator, not a spoken line.)
3. UEX: one server token now makes calls for every pilot. Worth a heads-up to your UEX contact
   about expected volume and rate limits.
4. Anthropic as a second fallback (v2): worth the translation layer, or is OpenAI enough?
