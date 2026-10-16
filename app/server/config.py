from functools import lru_cache

from pydantic import AliasChoices, ConfigDict, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # No database_url field here — db/session.py already reads TRADE_DB_URL directly
    # (same env var this project has always used) and the server just imports its
    # SessionLocal like every DB-touching module already does. Duplicating that into a
    # second config surface would just be two places that could disagree.

    # OAuth — Discord. Requires "Public Client" OFF in the Developer Portal (the old
    # client-side PKCE flow needed it on; a real server can hold a secret).
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://localhost:8000/auth/discord/callback"
    # Where /auth/discord/login may send the browser (with the JWT in the query string)
    # once login completes. Loopback http://localhost:<port> and http://127.0.0.1:<port>
    # (the desktop client's one-shot listener, auth/discord_identity.py) are always
    # allowed; this adds exact origins on top, e.g. ["https://heyalice.help"] for a future
    # web client. Env: AUTH_ALLOWED_REDIRECT_ORIGINS='["https://heyalice.help"]'.
    auth_allowed_redirect_origins: list[str] = []
    # Beta gating (routes/auth.py's discord_callback): only members of this Discord
    # server holding this role get a JWT. Empty BETA_GUILD_ID = gating off, scope stays
    # "identify" -- clearing it at release is the whole switch-off, nothing is stored per
    # user. Empty BETA_ROLE_ID with a guild set = membership of the server alone is enough.
    beta_guild_id: str = ""
    beta_role_id: str = ""

    # JWT
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    # UEX Corp — needed here now that the UEX cache's read-through-or-fetch logic lives
    # server-side (uex_cache_service.py). Every other UEXCorpClient method (live,
    # uncached lookups) stays client-side and never reads this.
    uexcorp_api_key: str = ""
    uexcorp_bearer_token: str = ""
    # UEX allows 7,000 requests a day per key (told to Jeff, 2026-10-16). server/uex_budget.py
    # counts every live call against it, per UTC day: UEX's own reset time isn't known,
    # which the margin partly covers. Past limit - margin, nothing calls UEX and the
    # cache serves stale rows where it has them.
    uex_daily_call_limit: int = 7000
    uex_call_margin: int = 200
    # The hourly route prefetch (server/uex_route_prefetch.py) stops once fewer than this
    # many calls are left today, so pilots' own lookups always have room. 0 = prefetch may
    # use everything up to the margin.
    uex_prefetch_reserve: int = 1500
    # How often every trade terminal's routes are refreshed ahead of any search. 161
    # terminals hourly is ~3,900 calls a day. 0 = no prefetch (searches fetch on demand,
    # as before). Must stay below the ROUTE_BY_ORIGIN lifetime in tools/uexcorp/price_cache.py,
    # or rows expire between runs.
    uex_route_prefetch_interval_s: int = 3600

    # LLM gateway (server/llm_gateway_service.py, docs/llm-gateway.md). heket is
    # the self-hosted Ollama box, reached over Tailscale -- never exposed to pilots.
    # The box was nicknamed "frankencompute" until 2026-10-16. The old FRANKENCOMPUTE_*
    # env names are still read (HEKET_* wins when both are set), so a deploy can't
    # silently drop to the localhost defaults below before prod's .env is updated.
    # Remove the second alias once /opt/graphling/.env only has HEKET_* names.
    heket_url: str = Field(
        "http://localhost:11434", validation_alias=AliasChoices("heket_url", "frankencompute_url"),
    )
    heket_chat_model: str = Field(
        "gemma4", validation_alias=AliasChoices("heket_chat_model", "frankencompute_chat_model"),
    )
    heket_classify_model: str = Field(
        "gemma4", validation_alias=AliasChoices("heket_classify_model", "frankencompute_classify_model"),
    )
    # The alice-chat-small role: short spoken lines (off-topic decline, social reply).
    # Empty = heket_chat_model, with thinking off.
    heket_small_chat_model: str = ""
    # The decision model behind /llm/decide/topic (server/topic_decision_service.py):
    # request, social or off-topic, before a turn is routed. Needs `ollama pull` on heket
    # and Ollama 0.40.4+. Empty = endpoint off (503), and clients fall back to the
    # alice-classify chat model.
    heket_topic_model: str = "tev1:4b"
    # The digest the role table was evaluated at. Empty = don't check. When set, a box
    # reporting a different digest for the model is treated as down (falls back) rather
    # than silently serving an unevaluated build.
    heket_model_digest: str = Field(
        "", validation_alias=AliasChoices("heket_model_digest", "frankencompute_model_digest"),
    )
    # Requests already running on heket before new ones overflow to fallback.
    # Matches the box's OLLAMA_NUM_PARALLEL=3 (docs/heket-bench.md): beyond 3,
    # a request would only queue there. 0 disables overflow (everything queues on the box).
    llm_max_inflight: int = 3

    # Fallback when heket can't answer: "anthropic" or "openai". No key for the
    # chosen provider = no fallback (503 instead). Empty model = that provider's default
    # (server/llm_gateway_service.DEFAULT_FALLBACK_MODELS).
    fallback_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    fallback_chat_model: str = ""
    fallback_classify_model: str = ""
    # Empty = fallback_chat_model, else the provider's default.
    fallback_small_chat_model: str = ""

    # TTS gateway (server/tts_service.py). Kokoro on heket over Tailscale
    # (heket/kokoro_server.py), e.g. http://100.x.y.z:8880. bf_isabella was
    # picked by ear over ElevenLabs and Chatterbox (docs/heket-bench.md).
    heket_tts_url: str = Field(
        "http://localhost:8880", validation_alias=AliasChoices("heket_tts_url", "frankencompute_tts_url"),
    )
    tts_voice: str = "bf_isabella"
    # Fallback when heket can't speak. ALICE then sounds different (a different
    # voice), so it's optional: no key = no fallback (503, ALICE stays quiet but still
    # answers in the overlay). The rate prices tts_usage rows; 0 while on the free tier,
    # set it to the plan's per-1k-character rate once paying.
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_usd_per_1k_chars: float = 0.0

    # Runaway guards (server/limits.py, docs/llm-gateway.md Limits) -- set well above any
    # real session; they exist to stop a broken client or a loop, not to ration use.
    # A normal turn is 2-3 LLM calls and 1 TTS call.
    llm_requests_per_minute: int = 30
    tts_requests_per_minute: int = 20
    # ~250 spoken replies a day per pilot; a 3-hour session is ~20k.
    tts_daily_chars_per_user: int = 50_000
    # Hosted-fallback spend across ALL pilots per UTC day, from llm_usage. Past it, an
    # outage means 503 instead of open-ended billing; a merely-busy heket just
    # queues instead of overflowing. 0 = no cap.
    fallback_daily_usd_cap: float = 5.0

    # ALICE's prompts (server/prompt_service.py) -- the gateway swaps them in, so pilots
    # never receive the text. Pulled from the LangSmith Hub when LANGSMITH_API_KEY is
    # set (plus LANGSMITH_ENDPOINT for this account's EU tenant), else the committed
    # snapshot baked into the image. A tag (e.g. "prod") makes a release deliberate:
    # only the commit carrying that tag in the Hub goes live; empty = latest commit.
    prompt_hub_tag: str = ""
    # How often the running server re-pulls, so a release reaches pilots without a
    # redeploy or restart. 0 = only at startup.
    prompt_refresh_s: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
